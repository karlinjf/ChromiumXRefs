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



#sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))
#import chromium_code_search as cs

# Use the below once the respository is renamed:
#from ChromiumXRefs.lib import chromium_code_search as cs

# TODO store the phantom's location so that update calls can use the same location
# TODO support multiple phantoms (probably need to store some phantom id in the links for lookup)


# =============== CODESEARCH CODE ===================

# A key/value store that stores objects to disk in temporary objects
# for 30 minutes.
class FileCache:
  def __init__(self):
    self.store = {}
    threading.Timer(15 * 60, self.gc).start();

  def put(self, url, data):
    f = tempfile.TemporaryFile();
    f.write(data);
    self.store[url] = (f, datetime.datetime.now());

  def get(self, url):
    if not url in self.store:
      return ''
    (f, timestamp) = self.store[url]
    f.seek(0);
    return f.read();

  def gc(self):
    threading.Timer(15 * 60, self.gc).start();
    expired = datetime.datetime.now() - datetime.timedelta(minutes=30);
    remove = []
    for url, (f, timestamp) in self.store.items():
      if timestamp < expired:
        remove.append(url)
    for url in remove:
      self.store.pop(url);

class CodeSearch:
  def __init__(self, should_cache):
    self.cache = None
    if should_cache:
      self.cache = FileCache();

  # Retrieve the url by first trying to cache and falling back to the network.
  def retrieve(self, url):
    if self.cache:
      cached_response = self.cache.get(url);
      if (cached_response):
        return cached_response.decode('utf8');

    response = None

    try:
      if len(url) > 1500:
        short_url = url.split('?')[0]
        data = url.split('?')[1]
        response = urllib.request.urlopen(short_url, data=data.encode('utf-8'), timeout=3)
      else:
        response = urllib.request.urlopen(url, timeout=3)

    except timeout:
      return ''
    except (HTTPError, URLError) as error:
      return ''

    result = response.read()
    if self.cache:
      self.cache.put(url, result);
    return result.decode('utf8');

  def getSignatureFor(self, src_file, method, line):
      url = ('https://cs.chromium.org/codesearch/json'
             '?annotation_request=b'
             '&file_spec=b'
             '&package_name=chromium'
             '&name={file_name}'
             '&file_spec=e'
             '&type=b'
             '&id=1'
             '&type=e'
             '&label='
             '&follow_branches=false'
             '&annotation_request=e')
      url = url.format(file_name=urllib.parse.quote(src_file, safe=''))

      result = self.retrieve(url);
      if not result:
        return ''

      result = json.loads(result)['annotation_response'][0]

      # First see if we can find the term on the given line
      for snippet in result.get('annotation', []):
        if not 'range' in snippet:
          continue
        range = snippet['range']
        if not range['start_line'] == line:
          continue

        if 'internal_link' in snippet:
          signature = snippet['internal_link']['signature']
          if method in signature:
            return signature
        if 'xref_signature' in snippet:
          signature = snippet['xref_signature']['signature']
          if method in signature:
            return signature

      # Next see if we can find the term within 10 lines
      for snippet in result.get('annotation', []):
        if not 'range' in snippet:
          continue
        range = snippet['range']
        if not abs(range['start_line'] - line) < 10:
          continue

        if 'internal_link' in snippet:
          signature = snippet['internal_link']['signature']
          if method in signature:
            return signature
        if 'xref_signature' in snippet:
          signature = snippet['xref_signature']['signature']
          if method in signature:
            return signature

      # Look for the term everywhere
      for snippet in result.get('annotation', []):
        if not 'type' in snippet:
          continue
        if 'xref_signature' in snippet:
          signature = snippet['xref_signature']['signature']
          if '%s(' % method in signature:
            return signature

        elif 'internal_link' in snippet:
          signature = snippet['internal_link']['signature']
          if '::%s' % method in signature or 'class-%s' % method in signature:
            return signature
      return ''

  def getCallGraphFor(self, signature):
      url = ('https://cs.chromium.org/codesearch/json'
           '?call_graph_request=b'
           '&signature={signature}'
           '&file_spec=b'
           '&package_name=chromium'
           '&name=.'
           '&file_spec=e'
           '&max_num_results=500'
           '&call_graph_request=e')
      url = url.format(signature=urllib.parse.quote(signature, safe=''))

      result = self.retrieve(url);
      if not result:
        return {}

      result = json.loads(result)['call_graph_response'][0];
      node = result['node'];

      callers = [];
      last_signature = ''
      if not 'children' in node:
        return callers
      for child in node['children']:
        if child['signature'] == last_signature:
          continue
        if not 'snippet_file_path' in child:
          continue

        caller = {}
        caller['filename'] = child['snippet_file_path'];
        caller['line'] = child['call_site_range']['start_line']
        caller['col'] = child['call_site_range']['start_column']
        caller['text'] = child['snippet']['text']['text']
        caller['calling_method'] = child['identifier']
        caller['calling_signature'] = child['signature']
        last_signature = child['signature']
        caller['display_name'] = child['display_name']
        callers.append(caller)
      return callers

  def getRefForMatch(self, filename, match):
    ref = {'filename': filename, 'line': match['line_number'], 'signature': match['signature']}
    if 'line_text' in match:
      ref['line_text'] = match['line_text']
    return ref;


  def getXrefsFor(self, signature):
      url = ('https://cs.chromium.org/codesearch/json'
             '?xref_search_request=b'
             '&query={signature}'
             '&file_spec=b'
             '&name=.'
             '&package_name=chromium'
             '&file_spec=e'
             '&max_num_results=500'
             '&xref_search_request=e')
      url = url.format(signature=urllib.parse.quote(signature, safe=''))
      result = self.retrieve(url);

      if not result:
        return {}

      result = json.loads(result)['xref_search_response'][0]
      status = result['status']
      if not 'search_result' in result:
          return {}
      search_results = result['search_result']

      xrefs = {}

      for file_result in search_results:
          filename = file_result['file']['name']
          for match in file_result['match']:
              if match['type'] == 'HAS_DEFINITION':
                  xrefs['definition'] = self.getRefForMatch(filename, match);
              elif match['type'] == 'HAS_DECLARATION':
                  xrefs['declaration'] = self.getRefForMatch(filename, match);
              elif match['type'] == 'OVERRIDDEN_BY':
                  xrefs.setdefault('overrides', []);
                  xrefs['overrides'].append(self.getRefForMatch(filename, match));
              elif match['type'] == 'REFERENCED_AT':
                  xrefs.setdefault('references', []);
                  xrefs['references'].append(self.getRefForMatch(filename, match));
      return xrefs

# =============== !CODESEARCH CODE ===================

g_cs = CodeSearch(True);

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
    global g_cs;
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
      print(cur_callers)
      caller = cur_callers[i]
      if 'callers' in caller:
        cur_callers = caller['callers']

    if (link_type == 'target'):
      goToLocation(self, self.src_path, caller, view);
    elif (link_type == 'expand'):
      caller['callers'] = g_cs.getCallGraphFor(caller['calling_signature'])
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
    global g_cs;
    self.selected_word = self.getWord(view);
    self.file_path = getRoot(self, view.file_name());
    if self.file_path == '':
      self.log("Could not find src/ directory in path", view);
      return '';
    self.src_path = posixPath(view.file_name().split(self.file_path)[0]);
    self.file_path = posixPath(self.file_path)

    self.selection_ref = {'line': self.selection_line, 'filename': self.file_path}

    self.signature = g_cs.getSignatureFor(self.file_path, self.selected_word, self.selection_line);
    return self.signature != ''

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
    global g_cs;

    self.show_tests = True;

    if not self.getSignatureForSelection(edit, view):
      self.log("Could not find signature for: " + self.selected_word, view);
      return;

    self.xrefs = g_cs.getXrefsFor(self.signature);
    if not self.xrefs:
      self.log("Could not find xrefs for: " + self.selected_word, view);
      return;

    self.callers = g_cs.getCallGraphFor(self.signature);

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

    xrefs = g_cs.getXrefsFor(self.signature);
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

    xrefs = g_cs.getXrefsFor(self.signature);
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
