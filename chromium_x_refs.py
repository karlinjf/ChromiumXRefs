# Copyright 2017 Josh Karlin. All rights reserved.
# Use of this source code is governed by the Apache license found in the LICENSE
# file.

# === CODESEARCH IMPORTS
import datetime
import json
from socket import timeout
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse

# === !CODESEARCH IMPORTS

import html
import html.parser
import imp
import os.path
import sys

import sublime, sublime_plugin

import ChromiumXRefs.third_party.codesearch.codesearch as codesearch

g_cs = None

def getCS(path=None):
  global g_cs
  if g_cs is None:
    if path is None:
      print("No g_cs found and unable to create one.")
      return None
    g_cs = codesearch.CodeSearch(should_cache=True, source_root=path)
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

def goToLocation(cmd, src_path, caller, view):
  line = caller['line'];
  path = src_path + caller['filename']
  view.window().open_file(path + ":%d:0" % line, sublime.ENCODED_POSITION)

def goToSelection(cmd, src_path, callers, sel, view):
  if sel < 0:
    return
  goToLocation(cmd, src_path, callers[sel], view)

class CXRefs:
  def __init__(self):
    self.data = {}
    print("Initializing")

  def getWord(self, view):
    for region in view.sel():
      if region.empty():
          # if we have no selection grab the current word
          word = view.word(region)

          # grab the word plus two characters before it
          word_plus = sublime.Region(word.a, word.b)
          word_plus.a -= 1;
          str_word_plus = view.substr(word_plus)
          if str_word_plus.startswith(":") or str_word_plus.startswith("~"):
            word = word_plus

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
          body += self.genHtmlImpl(caller['callers'], [loc] + location)
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
      body += '<a href=defined:>Definition</a>'

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
        body += "<li><a href=ref:%d:%s>%s</a></li>" % (ref['line'], html.escape(ref['filename']), html.escape(ref['line_text']));
      body += '</ul></ul></p>'

    if 'overrides' in xrefs:
      body += '<p><b>Overrides:</b><br><ul>'

      last_file = ''
      for ref in xrefs['overrides']:
        if ref['filename'] != last_file:
            if last_file != '':
              body += '</ul>';
            body += '<li>' + ref['filename'] + '</li><ul>';
            last_file = ref['filename'];
        body += "<li><a href=ref:%d:%s>%s</a></li>" % (ref['line'], html.escape(ref['filename']), html.escape(ref['line_text']));
      body += '</ul></ul></p>'

    body += "</body>"
    return body

  def getSignatureForSelection(self, edit, view):
    self.selected_word = self.getWord(view);
    root_path = getRoot(self, view.file_name());
    if root_path == '':
      self.log("Could not find src/ directory in path", view);
      return '';
    self.src_path = posixPath(view.file_name().split(root_path)[0]);

    self.selection_ref = {'line': self.selection_line, 'filename': root_path }

    # This is tricky, figure out
    file_path = posixPath(view.file_name())

    g_cs = getCS(self.src_path);

    # First see if we have an exact match at this location (e.g., unedited file)
    try:
      sig = g_cs.GetSignatureForLocation(file_path, self.selection_line, self.selection_column);
      if self.selected_word in sig:
        self.signature = sig
        print("Found signature: %s" % (sig))
        return True
    except Exception as e:
      x = 1  # do nothing

    # Otherwise grab the first thing that comes
    signatures = g_cs.GetSignaturesForSymbol(file_path, self.selected_word);
    if len(signatures) > 0:
      print("Found signature: %s" % (signatures[0]))
      self.signature = signatures[0]
    return self.signature != ''

  def getRefForXrefNode(self, node):
    return { 'filename': node.GetFile().Path(),
             'signature': node.GetSignature(),
             'line': node.single_match.line_number,
             'line_text': node.single_match.line_text }

  def getXrefsFor(self, signature):
    g_cs = getCS(self.src_path);

    results = {}

    node = codesearch.XrefNode.FromSignature(g_cs, signature);
    definitions = node.GetEdges(codesearch.EdgeEnumKind.HAS_DEFINITION)
    if definitions:
      results['definition'] = self.getRefForXrefNode(definitions[0])

    declarations = node.GetEdges(codesearch.EdgeEnumKind.HAS_DECLARATION)
    if declarations:
      results['declaration'] = self.getRefForXrefNode(declarations[0])

    overrides = node.GetEdges(codesearch.EdgeEnumKind.OVERRIDDEN_BY)
    results['overrides'] = []
    for override in overrides:
      results['overrides'].append(self.getRefForXrefNode(override))

    references = node.GetEdges(codesearch.EdgeEnumKind.REFERENCED_AT)
    results['references'] = []
    for reference in references:
      results['references'].append(self.getRefForXrefNode(reference))

    return results

  def getCallGraphFor(self, signature):
    g_cs = getCS(self.src_path);
    results = []



    # Add x-refs as callers too
    node = codesearch.XrefNode.FromSignature(g_cs, signature);
    references = node.GetEdges(codesearch.EdgeEnumKind.REFERENCED_AT)
    for reference in references:
      # Get the annotations for the file, and find the closest function definition to
      # the line that has the reference
      csfile = reference.GetFile()
      line = reference.single_match.line_number
      snippet = reference.single_match.line_text


      print("Finding closest to: %d" % line)
      annotations = csfile.GetAnnotations()
      closest_line = -1
      closest_node = None
      for annotation in annotations:
        if not annotation.xref_kind == codesearch.NodeEnumKind.METHOD:
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

      if closest_line > -1:
        print("Closest line = %d" % closest_line)
        # This is the closest method to the line that the xref is on
        sig = closest_node.xref_signature.signature
        method_name = sig.split("(")[0]
        method_name = method_name.split("::")[-1]
        method_name = method_name.split(" ")[-1]
        call = {
          'filename': csfile.Path(),
          'line': line,
          'col': 0,
          'text': snippet,
          'calling_method': method_name,
          'calling_signature': sig,
          'display_name': method_name
        }
        results.append(call)


    # return { 'filename': node.GetFile().Path(),
    #          'signature': node.GetSignature(),
    #          'line': node.single_match.line_number,
    #          'line_text': node.single_match.line_text }



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

    for caller in node.children:
      if caller.signature == last_signature:
        continue
      if not caller.snippet_file_path:
        continue

      last_signature = caller.signature

      call = { 'filename': caller.file_path,
               'line': caller.call_site_range.start_line,
               'col': caller.call_site_range.start_column,
               'text': caller.snippet.text.text,
               'calling_method': caller.identifier,
               'calling_signature': caller.signature,
               'display_name': caller.display_name
             }
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

    self.xrefs = self.getXrefsFor(self.signature);
    if not self.xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    self.callers = self.getCallGraphFor(self.signature);

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
    xrefs = self.getXrefsFor(self.signature);
    if not xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    if 'declaration' in xrefs:
      goToLocation(self, self.src_path, xrefs['declaration'], view)
    elif 'definition' in xrefs:
      goToLocation(self, self.src_path, xrefs['definition'], view);
    else:
      self.log("Couldn't find a reference to jump to");
      return;

  def jumpToDefinition(self, edit, view):
    window = view.window();

    if not self.getSignatureForSelection(edit, view):
      self.log("Could not find signature for: " + self.selected_word, view);
      return;

    g_cs = getCS();
    xrefs = self.getXrefsFor(self.signature);
    if not xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    if 'definition' in xrefs:
      goToLocation(self, self.src_path, xrefs['definition'], view);
    elif 'declaration' in xrefs:
      goToLocation(self, self.src_path, xrefs['declaration'], view)
    else:
      self.log("Couldn't find a reference to jump to");
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
