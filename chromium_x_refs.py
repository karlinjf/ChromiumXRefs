# Copyright 2017 Josh Karlin. All rights reserved.
# Use of this source code is governed by the Apache license found in the LICENSE
# file.

import datetime
import html
import html.parser
import imp
import os.path
import re
import sys
import time
from pprint import pprint
import sublime, sublime_plugin

import ChromiumXRefs.third_party.codesearch as codesearch

g_cs = None
g_last_gcd_g_cs = datetime.datetime.now()


def fullprint(obj):
  pprint(vars(obj))

def getCS(path=None):
  global g_cs
  global g_last_gcd_g_cs

  create = False

  if g_cs is None:
    if path is None:
      print("No g_cs found and unable to create one.")
      return None
    create = True
  if not path is None and (datetime.datetime.now() - g_last_gcd_g_cs).seconds > 60 * 10:
    # The codesearch object collects cruft over time. The easiest way to deal with that is to periodically delete it.
    create = True

  if create:
    g_cs = codesearch.CodeSearch(should_cache=True, source_root=path)
    g_last_gcd_g_cs = datetime.datetime.now()

  return g_cs

g_last_xref_cmd = None  # The last chromium cmd that ran

def posixPath(path):
  if os.path.sep == '\\':
    return path.replace('\\','/');
  return path;

def getRoot(cmd, path):
  src_split = path.split('src')
  src_count = len(src_split)
  if src_count < 2:
    return ''
  if src_count == 2:
    return 'src' + path.split('src')[1]

  # There are multiple 'src' directories in the path, figure out which one is
  # the root of the tree by taking the closest to the filesystem root with a
  # .git subdirectory.
  rootPath = ''
  for partial in src_split:
    rootPath += partial
    rootPath += 'src'
    if os.path.isdir(rootPath + '/.git'):
      return 'src' + path.split(rootPath)[1]
  return ''

g_open_callbacks_on_load = {}

class EventListener(sublime_plugin.EventListener):
    # Called when a file is finished loading.
    def on_load_async(self, view):
        global g_open_callbacks_on_load
        if view.file_name() in g_open_callbacks_on_load:
            g_open_callbacks_on_load[view.file_name()]()
            del g_open_callbacks_on_load[view.file_name()]


def goToLocation(cmd, src_path, caller, view):
  line = caller['line'];
  path = src_path + caller['filename']

  # Open the file and jump to the line
  new_view = view.window().open_file(path + ":%d:0" % line, sublime.ENCODED_POSITION)
  if new_view.is_loading():
    global g_open_callbacks_on_load
    g_open_callbacks_on_load[path] = lambda: finishGoingToLocation(caller, new_view)
    return

  finishGoingToLocation(caller, new_view)

def finishGoingToLocation(caller, view):
  # Highlight the text
  position = view.sel()[0].a
  line = view.substr(view.line(position))
  view.sel().clear()
  view.sel().add(view.line(position))

  if (caller['text'] in line):
    return

  # Find where the line might be
  regions = view.find_all(caller['text'], sublime.LITERAL)

  if not regions:
    return

  # Find the closest region to our current position
  closest_region = None

  for region in regions:
    if closest_region is None:
      closest_region = region
      continue

    if abs(region.a - closest_region.a) < abs(closest_region.a - position):
      closest_region = region

  view.show_at_center(closest_region)
  view.sel().clear()
  view.sel().add(closest_region)

def goToSelection(cmd, src_path, callers, sel, view):
  if sel < 0:
    return
  goToLocation(cmd, src_path, callers[sel], view)

class CXRefs:
  def __init__(self):
    self.data = {}
    self.in_mojo = False

  def getWord(self, view):
    for region in view.sel():
      if region.empty():
          # if we have no selection grab the current word
          word = view.word(region)

          # if view.substr(sublime.Region(word.a-2, word.b)).startswith("::"):
          #   word = sublime.Region(word.a-2, word.b)
          # elif view.substr(sublime.Region(word.a-1, word.b)).startswith("~"):
          #   word = sublime.Region(word.a-1, word.b)

          # if view.substr(sublime.Region(word.a, word.b+1)).endswith("("):
          #   word = sublime.Region(word.a, word.b+1)

          if not word.empty():
              self.selection_line = view.rowcol(region.a)[0]+1;
              self.selection_column = view.rowcol(region.a)[1];
              return view.substr(word)

  def createPhantom(self, doc, view):
    xref_data = self.data[view.window().id()];
    loc = sublime.Region(0,0);
    return sublime.Phantom(loc, doc, sublime.LAYOUT_BELOW, lambda link: self.processLink(link, self.callers, view));

  def updatePhantom(self, phantom, view):
    xref_data = self.data[view.window().id()];
    xref_data['phantom_set'].update([phantom])

  def destroyPhantom(self, view):
    xref_data = self.data[view.window().id()];
    xref_data['phantom_set'].update([])
    view.window().run_command("hide_panel", {"panel": "output.chromium_x_refs"})

  def processLink(self, link, callers, view):
    g_cs = getCS();
    link_type = link.split(':')[0]

    if link_type == 'selected_word':
      goToLocation(self, self.src_path, self.selection_ref, view);
      return;

    if link_type == 'declared':
      goToLocation(self, self.src_path, self.xrefs['declaration'], view);
      return;

    if link_type == 'defined':
      goToLocation(self, self.src_path, self.xrefs['definition'], view);
      return;

    if link_type == 'overrides':
      goToLocation(self, self.src_path, self.xrefs['overrides'], view);
      return;

    if link_type == 'ref':
      ref = {}
      ref['line'] = int(link.split(':')[1])
      ref['filename'] = html.parser.HTMLParser().unescape(''.join(link.split(':')[2:]));
      goToLocation(self, self.src_path, ref, view);
      return;

    if link_type == 'filter':
      if link.split(':')[1] == 'test':
        self.show_tests = False;
        doc = self.genHtml();
        self.updatePhantom(self.createPhantom(doc, view), view);
        return;

    if link_type == 'nofilter':
      if link.split(':')[1] == 'test':
        self.show_tests = True;
        doc = self.genHtml()
        self.updatePhantom(self.createPhantom(doc, view), view);
        return;

    if link_type == 'killPhantom':
      self.destroyPhantom(view);
      return;

    str_loc = link.split(':')[1]
    loc = [int(x) for x in str_loc.split(',')]
    cur_callers = callers
    caller = None
    for i in loc:
      caller = cur_callers[i]
      if 'callers' in caller:
        cur_callers = caller['callers']

    if (link_type == 'target'):
      goToLocation(self, self.src_path, caller, view);
    elif (link_type == 'expand'):
      caller['callers'] = self.getCallGraphFor(caller['calling_signature'])
      doc = self.genHtml()
      self.updatePhantom(self.createPhantom(doc, view), view);

    elif (link_type == 'shrink'):
      caller.pop('callers')
      doc = self.genHtml()
      self.updatePhantom(self.createPhantom(doc, view), view);

    elif (link_type == 'filter'):
      caller.pop('callers')
      doc = self.genHtml()
      self.updatePhantom(self.createPhantom(doc, view), view);

    # DO something
    link = 1

  def genHtmlImpl(self, callers, location):
    if not callers:
      return ""

    loc = 0
    body = "<ul>"
    for caller in callers:
      full_loc = location + [loc]
      str_loc = ','.join([str(x) for x in full_loc])
      if 'callers' in caller:
        link_expander = "<a id=chromium_x_ref_expander href=shrink:" + str_loc + '>-</a>'
      else:
        link_expander = "<a id=chromium_x_ref_expander href=expand:" + str_loc + '>+</a>'

      calling_method = caller['display_name'].split('(')[0]

      link_target = "<a href=target:%s>%s</a>" % (str_loc, html.escape(calling_method))
      if self.show_tests or not 'test' in calling_method.lower():
        body += "<li>%s %s</li>" % (link_expander, link_target)
        if 'callers' in caller:
          body += self.genHtmlImpl(caller['callers'], location + [loc])
      loc += 1

    body += "</ul>"
    return body


  def genHtml(self):
    body = """
    <body id=chromium_x_refs_body>
    <style>
    body {
      background-color: color(var(--background) blend(gray 90%));
      color: var(--foreground);
      border-radius: 5pt;
    }
    * {
      font-size: 12px;
    }
    #chromium_x_ref_expander {
      color: var(--redish);
      padding: 5px;
    }
    ul {
      margin-top: 0px;
      padding-top: 5px;
      margin-bottom: 0px;
      padding-bottom: 5px;
      padding-left: 15px;
      margin-left: 0px;
      white-space: nowrap;
      list-style-type: none;
    }
    #hline {
      background-color: color(var(--foreground) blend(gray 10%);
      font-size: 1px;
      margin-top: 4px;
    }
    </style>
    """

    tab = '&nbsp;' * 4;
    body += "<div class=navbar>";
    xrefs = self.xrefs;

    body += '<b> <a href=selected_word>' + self.selected_word + '</a></b>' + tab
    if 'declaration' in xrefs:
      body += '<a href=declared:>Declaration</a>' + tab
    if 'definition' in xrefs:
      body += '<a href=defined:>Definition</a>' + tab
    if 'overrides' in xrefs:
      body += '<a href=overrides:>Overrides</a>'


    body += tab;

    if self.show_tests:
      body += '<a id=chromium_x_ref_filter href=filter:test>[-Tests]</a>'
    else:
      body += '<a id=chromium_x_ref_filter href=nofilter:test>[+Tests]</a>'

    body += tab
    body += '<a href=killPhantom>[X]</a>'
    body += "</div>"

    # Add a horizontal line
    body += '<div id=hline>.</div>'

    if self.callers:
      body += '<p><b>Callers:</b><br>'
      body += self.genHtmlImpl(self.callers, [])
      body += '</p>'


    if 'references' in xrefs:
      body += '<p><b>References:</b><br><ul>'

      last_file = ''
      for ref in xrefs['references']:
        if not self.show_tests and 'test' in ref['filename'].lower():
          continue
        if ref['filename'] != last_file:
            if last_file != '':
              body += '</ul>';
            body += '<li>' + ref['filename'] + '</li><ul>';
            last_file = ref['filename'];
        body += "<li><a href=ref:%d:%s>%s</a></li>" % (ref['line'], html.escape(ref['filename']), html.escape(ref['text']));
      if last_file:
        body += '</ul>'
      body += '</ul></p>'

    if 'overridden' in xrefs:
      body += '<p><b>Overridden by:</b><br><ul>'

      last_file = ''
      for ref in xrefs['overridden']:
        if ref['filename'] != last_file:
            if last_file != '':
              body += '</ul>';
            body += '<li>' + ref['filename'] + '</li><ul>';
            last_file = ref['filename'];
        body += "<li><a href=ref:%d:%s>%s</a></li>" % (ref['line'], html.escape(ref['filename']), html.escape(ref['text']));
      if last_file:
        body += '</ul>'
      body += '</ul></p>'

    body += "</body>"
    return body


  def getSignatureForSelection(self, edit, view):
    self.signature = ''
    self.selected_word = self.getWord(view);
    # clean_word = self.selected_word.replace(':', '')
    # clean_word = clean_word.replace('~', '')
    # clean_word = clean_word.replace('(', '')
    abs_file = posixPath(os.path.abspath(os.path.realpath(view.file_name())))

    root_path = getRoot(self, abs_file);
    if root_path == '':
      self.log("Could not find src/ directory in path", view);
      return '';
    self.src_path = abs_file.split(root_path)[0]

    self.selection_ref = {'line': self.selection_line, 'filename': root_path }

    g_cs = getCS(self.src_path);

    # # First see if we have an exact match at this location (e.g., unedited file)
    # try:
    #   sig = g_cs.GetSignatureForLocation(abs_file, self.selection_line, self.selection_column);
    #   if self.selected_word in sig:
    #     self.signature = sig
    #     return True
    # except Exception as e:
    #   x = 1  # do nothing

    # # Grab the nearest signature. A signature exactly in the right line and column wins.
    # signature = ''
    # file_info = g_cs.GetFileInfo(abs_file)
    # closest_line = 99999999
    # print("Searching for signature: %s" % self.selected_word)
    # for annotation in file_info.GetAnnotations():
    #   sig = ''
    #   if hasattr(annotation, 'xref_signature'):
    #     sig = annotation.xref_signature.signature

    #   if hasattr(annotation, 'internal_link'):
    #     sig = annotation.internal_link.signature

    #   if not sig:
    #     continue


    #   if self.selected_word in sig and clean_word in file_info.Text(annotation.range):
    #     annotation_line = annotation.range.start_line
    #     if abs(annotation_line - self.selection_line) < closest_line or annotation.range.Contains(self.selection_line, self.selection_column):
    #       signature = sig
    #       closest_line = abs(annotation_line - self.selection_line)

    # self.signature = signature
    # return self.signature != ''

    # Grab the nearest signature. A signature exactly in the right line and column wins.


    signature = ''
    file_info = g_cs.GetFileInfo(abs_file)
    closest_line = 99999999
    print("Searching for signature: %s" % self.selected_word)
    for annotation in file_info.GetAnnotations():
      sig = ''
      if hasattr(annotation, 'xref_signature'):
        sig = annotation.xref_signature.signature

      if hasattr(annotation, 'internal_link'):
        sig = annotation.internal_link.signature

      if not sig:
        continue

      if self.selected_word in file_info.Text(annotation.range):
        annotation_line = annotation.range.start_line
        if abs(annotation_line - self.selection_line) < closest_line or annotation.range.Contains(self.selection_line, self.selection_column):
          signature = sig
          closest_line = abs(annotation_line - self.selection_line)

    self.signature = signature
    return self.signature != ''

  def getRefForXrefNode(self, node):
    return { 'filename': node.filespec.name,
             'signature': node.GetSignature(),
             'line': node.single_match.line_number,
             'text': node.single_match.line_text }

  def getXrefsFor(self, signature):
    g_cs = getCS(self.src_path);

    results = {'overridden':[], 'references':[]}

    node = codesearch.XrefNode.FromSignature(g_cs, signature);
    refs = node.Traverse([codesearch.KytheXrefKind.DEFINITION,
      codesearch.KytheXrefKind.DECLARATION,
      codesearch.KytheXrefKind.OVERRIDES,
      codesearch.KytheXrefKind.OVERRIDDEN_BY,
      codesearch.KytheXrefKind.REFERENCE]);

    if not refs:
      return results

    xref_nodes = []
    for n in refs:
      xref = self.getRefForXrefNode(n)
      if n.single_match.type == 'DEFINITION':
        results['definition'] = xref
      elif n.single_match.type == 'DECLARATION':
        results['declaration'] = xref
      elif n.single_match.type == 'OVERRIDES':
        results['overrides'] = xref
      elif n.single_match.type == 'OVERRIDDEN_BY':
        results['overridden'].append(xref)
      elif n.single_match.type == 'REFERENCE':
        results['references'].append(xref)
        xref_nodes.append(n)

    return (results, xref_nodes)

  def getEnclosingMethod(self, edge):
    g_cs = getCS();

    # Get the annotations for the file, and find the closest function definition to
    # the line that has the reference
    try:
      csfile = edge.GetFile()
    except Exception as e:
      return None

    line = edge.single_match.line_number
    snippet = edge.single_match.line_text

    annotations = csfile.GetAnnotations()
    closest_line = -1
    closest_node = None
    for annotation in annotations:
      if not annotation.kythe_xref_kind == codesearch.KytheNodeKind.FUNCTION:
        continue
      if not hasattr(annotation, 'xref_signature'):
        continue
      if '\\.h' in annotation.xref_signature.signature:
        # We want methods defined in this file, that make the xref
        continue
      annotation_line = annotation.range.start_line
      if annotation_line > closest_line and annotation_line < line:
        closest_line = annotation_line
        closest_node = annotation

    return closest_node

  def GetSignaturesForSearchSymbol(self, g_cs, filename, symbol, xref_kind=None):
    print("Processing file: %s" % filename.name)
    file_info = g_cs.GetFileInfo(filename)
    signatures = set()
    for annotation in file_info.GetAnnotations():
      if not hasattr(annotation, 'xref_signature'):
        continue
      if not annotation.kythe_xref_kind == codesearch.KytheNodeKind.FUNCTION:
        continue

      if annotation.xref_signature.signature in signatures:
        continue

      if not symbol in annotation.xref_signature.signature:
        continue

      signatures.add(annotation.xref_signature.signature)

    return list(signatures)


  def SearchForSymbol(self, g_cs, symbol):
    search_response = g_cs.SendRequestToServer(
        codesearch.CompoundRequest(search_request=[
            codesearch.SearchRequest(
                query="function:" + symbol,
                exhaustive=True,
                max_num_results=50,
                return_all_duplicates=False,
                return_all_snippets=False,
                return_decorated_snippets=False,
                return_directories=False,
                return_line_matches=False,
                return_snippets=False)
        ])).search_response[0]

    if not hasattr(search_response, 'search_result'):
      return []

    signatures = []
    for result in search_response.search_result:

      if not hasattr(result, 'top_file'):
        continue

      for sig in self.GetSignaturesForSearchSymbol(g_cs, result.top_file.file, "class-" + symbol + "("):
        signatures.append(sig)

    return signatures

  def GetMojoCaller(self, caller, results, signature):
      if self.in_mojo:
        # Prevent infinite recursion. We call getCallGraphFor later in this
        # call and it can easily wind up back here.
        return True
      self.in_mojo = True
      g_cs = getCS(self.src_path);
      csfile = g_cs.GetFileInfo(self.src_path+caller.file_path)
      line = caller.call_site_range.start_line

      service_method = signature.split('(')[0].split('::')[-1]
      service_class = caller.display_name.split('::AcceptWithResponder(')[0].split('::')[-1].replace('StubDispatch', '')
      search_term = service_class + '::' + service_method



      print("Mojo caller: %s" % caller)
      print("Signature: %s" % signature)
      print("search term: %s" % search_term)

      sigs = self.SearchForSymbol(g_cs, search_term)

      # We may have multiple symbols, e.g., one for background_sync.mojom.h and one for background_sync.mojom-blink.h
      # Dear god what a hack, let's go with the longer one...
      longest = 0
      final_sig = None
      print("SIg options: ")
      for sig in sigs:
        if not '.mojom-' in sig:
          continue
        if not 'decl' in sig:
          continue
        final_sig = sig
        break

      if not final_sig:
        return False

      # Now we have the function that the client end of the mojo call should call
      # Recurse!
      print("Final sig: %s" % final_sig)
      callers = self.getCallGraphFor(final_sig, references=None)
      for caller in callers:
        caller['display_name'] = "mojo: " + caller['display_name']
        caller['calling_method'] = "mojo: " + caller['calling_method']
        results.append(caller)

      self.in_mojo = False
      return len(callers) > 0

  def GetDoLoopCaller(self, caller, results):
      g_cs = getCS(self.src_path);
      csfile = g_cs.GetFileInfo(self.src_path+caller.file_path)
      line = caller.call_site_range.start_line

      annotations = csfile.GetAnnotations()
      closest_line = -1
      closest_enum = None
      found = False
      for annotation in annotations:
        if not annotation.kythe_xref_kind == codesearch.KytheNodeKind.CONSTANT:
          continue
        if not hasattr(annotation, 'internal_link'):
          continue
        if not 'STATE' in annotation.internal_link.signature:
          continue
        # if not hasattr(annotation, 'xref_signature'):
        #   continue

        #if '\\.h' in annotation.xref_signature.signature:
        #  # We want methods defined in this file, that make the xref
        #  continue
        annotation_line = annotation.range.start_line
        if annotation_line > closest_line and annotation_line < line:
          closest_line = annotation_line
          closest_enum = annotation

      if closest_line > -1:
        # This is the closest enum constant to the doloop caller, assume
        # that this is the state enum that gets us here. Now figure out
        # where the state is set, that's our caller.
        node = codesearch.XrefNode.FromSignature(g_cs, closest_enum.internal_link.signature);

        refs = node.Traverse([codesearch.KytheXrefKind.REFERENCE], max_num_results=100);

        for ref in refs:
          if not ref.single_match.node_type == 'USAGE':
            continue
          if 'case' in ref.single_match.line_text:
            continue
          if '==' in ref.single_match.line_text:
            continue

          method = self.getEnclosingMethod(ref)
          if method is None:
            continue
          print(method)

          method_name = method.xref_signature.signature.split("(")[0]
          method_name = method_name.replace("class-", "")
          method_name = method_name.replace("cpp:", "")
          method_name = "doloop: " + method_name

          call = {
            'filename': ref.filespec.name,
            'line': ref.single_match.line_number,
            'col': 0,
            'calling_signature': method.xref_signature.signature,
            'text': ref.single_match.line_text,
            'display_name': method_name,
            'calling_method': method_name
          }

          results.append(call)
          found = True

      return found

  def GetIPCCaller(self, call, reference, results):
    g_cs = getCS(self.src_path);
    line = reference.single_match.line_number
    line_text = reference.single_match.line_text
    # line text: IPC_MESSAGE_HANDLER(CacheStorageHostMsg_CacheStorageKeys, OnCacheStorageKeys)

    # Get the signature for the message
    csfile = reference.GetFile()
    # Replace GetAnnotationsForFile code with csfile.GetAnnotations() once LINK_TO_DEFINITION is in the library
    response = g_cs.GetAnnotationsForFile(csfile.GetFileSpec(),
      [codesearch.AnnotationType(id=codesearch.AnnotationTypeValue.LINK_TO_DEFINITION)])
    if not hasattr(response.annotation_response[0], 'annotation'):
      return False
    annotations = response.annotation_response[0].annotation

    closest_line = -1
    message_ref = None
    for annotation in annotations:
      # TODO: THIS IS NOW BROKEN, what is TYPE_ALIAS in codesearch.KytheNodeKind.XXX ?
      if not annotation.kythe_xref_kind == codesearch.NodeEnumKind.TYPE_ALIAS:
        continue
      if not hasattr(annotation, 'internal_link'):
        continue

      annotation_line = annotation.range.start_line
      if annotation_line > closest_line and annotation_line <= line:
        closest_line = annotation_line
        message_ref = annotation

    if closest_line == -1:
      return False

    found = False

    # Get xrefs for the signature
    node = codesearch.XrefNode.FromSignature(g_cs, message_ref.internal_link.signature)
    refs = node.Traverse(codesearch.KytheXrefKindEnumKind.REFERENCE)
    for ref in refs:
      if not ref.single_match.node_type == 'USAGE':
        continue
      if not 'new' in ref.single_match.line_text:
        continue
      method = self.getEnclosingMethod(ref)
      if method is None:
        continue

      method_name = method.xref_signature.signature.split("(")[0]
      method_name = method_name.replace("class-", "")
      method_name = method_name.replace("cpp:", "")
      method_name = "ipc: " + method_name

      call = {
        'filename': ref.filespec.name,
        'line': ref.single_match.line_number,
        'col': 0,
        'calling_signature': method.xref_signature.signature,
        'text': ref.single_match.line_text,
        'display_name': method_name,
        'calling_method': method_name
      }
      results.append(call)
      found = True

    return found


  # This is heuristic black magic to transform a calling signature into a
  # printable name in a call graph. Avert your eyes and burn it with fire!
  def getCallingMethodNameFromSignature(self, caller):
      xref_node = codesearch.XrefNode.FromSignature(g_cs, caller.signature);

      # Get the text of the caller scope (e.g., the calling function name)
      file_info = g_cs.GetFileInfo(self.src_path + caller.snippet_file_path)
      range = caller.call_scope_range
      range.end_column = range.start_column + 100
      caller_text = file_info.Text(range).strip()

      # Let's see if we can find out some definition information about this caller
      caller_definitions = []
      try:
        caller_definitions = xref_node.GetRelatedDefinitions()
      except Exception as e:
        caller_definitions = []

      # No info, so try to infer what we can from caller_text
      if not caller_definitions:
        # Let's see if it's a test..
        if 'TEST_F' in caller_text or 'TEST_P' in text:
          test_class = text.split(",")[0].split("(")[-1].strip()
          test_name = text.split(",")[1].split(")")[0].strip()
          return test_class + "::" + test_name
        if 'TEST' in caller_text:
          test_name = text.split('[')[-1].split(']')[0]
          return "TEST::" + test_name

        # Give up and fall back to the filename
        file_name = caller.file_path.split('/')[-1]
        return file_name + '::' + caller_text


      caller_definition = caller_definitions[-1].single_match;

      if 'class' in caller_definition.line_text:
        print(caller_definition.line_text)
        caller_class = caller_definition.line_text.split(":")[0].strip()
        print(caller_class)
        caller_class = caller_class.split(" ")[-1].strip()
        print(caller_class)
        return caller_class + '::' + caller_text

      return file_name + '::' + caller_text



  def getCallingMethodNameFromRange(self, file_info, range):
      # range.start_column = 1
      if range.end_column == 0:
        range.end_column = 50
      return file_info.Text(range).strip()

  def getCallGraphFor(self, signature, references=None):
    g_cs = getCS(self.src_path);
    results = []


    response = g_cs.SendRequestToServer(
      codesearch.CompoundRequest(call_graph_request=[codesearch.CallGraphRequest(
        file_spec=g_cs.GetFileSpec(),
        max_num_results=500,
        signature=signature)
      ]))

    last_signature = ''
    if not response.call_graph_response:
      return results

    node = response.call_graph_response[0].node
    if not hasattr(node, 'children'):
      return results

    calling_methods = set()

    for caller in node.children:
      if caller.signature == last_signature:
        continue
      if not caller.snippet_file_path:
        continue

      last_signature = caller.signature

      handled = False
      if 'DoLoop' in caller.identifier:
        handled = self.GetDoLoopCaller(caller, results)

      calling_method = self.getCallingMethodNameFromSignature(caller)

      if not handled and 'Dispatch::AcceptWithResponder' in calling_method:
        handled = self.GetMojoCaller(caller, results, signature)

      if not handled:
        call = { 'filename': caller.file_path,
                 'line': caller.call_site_range.start_line,
                 'col': caller.call_site_range.start_column,
                 'text': caller.snippet.text.text,
                 'calling_method': caller.identifier,
                 'calling_signature': caller.signature,
                 'display_name': calling_method
               }
        calling_methods.add(calling_method)
        results.append(call)

    # Add x-refs as callers too
    signature_node = codesearch.XrefNode.FromSignature(g_cs, signature);
    if references is None:
      references = signature_node.Traverse()

    if len(references) < 10:
      for reference in references:
        method_node = self.getEnclosingMethod(reference)
        if not method_node is None:
          # This is the closest method to the line that the xref is on
          closest_sig = method_node.xref_signature.signature
          method_name = self.getCallingMethodNameFromRange(reference.GetFile(), method_node.range)
          if method_name in calling_methods:
            continue
          calling_methods.add(method_name)
          method_name = "ref: " + method_name

          call = {
            'filename': reference.GetFile().Path(),
            'line': reference.single_match.line_number,
            'col': 0,
            'text': reference.single_match.line_text,
            'calling_signature': closest_sig,
            'display_name': method_name
          }

          handled = False
          if 'OnMessageReceived' in method_name:
            handled = self.GetIPCCaller(call, reference, results)
          if not handled:
            results.append(call)

    return results


  def log(self, msg, view):
      print(msg);
      view.window().status_message(msg);

  def initWindow(self, window):
    if not window.id() in self.data:
      self.data[window.id()] = {}
      xref_data = self.data[window.id()];
      window.destroy_output_panel("chromium_x_refs");
      xref_data['panel'] = window.create_output_panel("chromium_x_refs", False);
      xref_data['phantom_set'] = sublime.PhantomSet(xref_data['panel'], "phantoms");

  def displayXRefs(self, edit, view):

    self.show_tests = True;

    if not self.getSignatureForSelection(edit, view):
      self.log("Could not find signature for: " + self.selected_word, view);
      return;

    g_cs = getCS();

    (self.xrefs, xref_nodes) = self.getXrefsFor(self.signature);
    if not self.xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    self.callers = self.getCallGraphFor(self.signature, xref_nodes);
    doc = self.genHtml();

    window = view.window();
    self.initWindow(window);

    self.updatePhantom(self.createPhantom(doc, view), view);
    window.run_command("show_panel", {"panel": "output.chromium_x_refs"})

  def recallXRefs(self, edit, view):
    window = view.window();
    self.initWindow(window);
    doc = self.genHtml();

    self.updatePhantom(self.createPhantom(doc, view), view);
    window = view.window();
    window.run_command("show_panel", {"panel": "output.chromium_x_refs"})

  def jumpToDeclaration(self, edit, view):
    window = view.window();

    # NOTE THAT THIS CALL OVERWRITES A BUNCH OF self VALUES WHICH MEANS THAT RECALL WILL BE BROKEN.
    # TODO: CHANGE THIS FUNCTION TO NOT SET VALUES IN SELF
    if not self.getSignatureForSelection(edit, view):
      self.log("Could not find signature for: " + self.selected_word, view);
      return;

    g_cs = getCS();
    (xrefs, refs) = self.getXrefsFor(self.signature);
    if not xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    if 'declaration' in xrefs:
      goToLocation(self, self.src_path, xrefs['declaration'], view)
    elif 'definition' in xrefs:
      goToLocation(self, self.src_path, xrefs['definition'], view);
    else:
      self.log("Couldn't find a reference to jump to", view);
      return;

  def jumpToDefinition(self, edit, view):
    window = view.window();

    if not self.getSignatureForSelection(edit, view):
      self.log("Could not find signature for: " + self.selected_word, view);
      return;

    g_cs = getCS();
    (xrefs, refs) = self.getXrefsFor(self.signature);
    if not xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    if 'definition' in xrefs:
      goToLocation(self, self.src_path, xrefs['definition'], view);
    elif 'declaration' in xrefs:
      goToLocation(self, self.src_path, xrefs['declaration'], view)
    else:
      self.log("Couldn't find a reference to jump to", view);
      return;

g_cxrefs = CXRefs()

class ChromiumXrefsCommand(sublime_plugin.TextCommand):
  def __init__(self, view):
    # Called once per view when you enter the view
    self.view = view;

  def run(self, edit):
    global g_cxrefs;
    g_cxrefs.displayXRefs(edit, self.view);

class ChromiumRecallXrefsCommand(sublime_plugin.TextCommand):
  def __init__(self, view):
    # Called once per view when you enter the view
    self.view = view;

  def run(self, edit):
    global g_cxrefs;
    g_cxrefs.recallXRefs(edit, self.view);

class ChromiumXrefsJumpToDeclarationCommand(sublime_plugin.TextCommand):
  def __init__(self, view):
    # Called once per view when you enter the view
    self.view = view;

  def run(self, edit):
    global g_cxrefs;
    g_cxrefs.jumpToDeclaration(edit, self.view)

class ChromiumXrefsJumpToDefinitionCommand(sublime_plugin.TextCommand):
  def __init__(self, view):
    # Called once per view when you enter the view
    self.view = view;

  def run(self, edit):
    global g_cxrefs;
    g_cxrefs.jumpToDefinition(edit, self.view)
