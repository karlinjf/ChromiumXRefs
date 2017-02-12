# Copyright 2017 Josh Karlin. All rights reserved.
# Use of this source code is governed by the Apache license found in the LICENSE
# file.

import getopt
import html
import html.parser
import json
import os.path
import sys
import urllib.request
import urllib.parse

import sublime, sublime_plugin

gLastChromeCmd = None  # The last chromium cmd that ran

# TODO store the phantom's location so that update calls can use the same location
# TODO support multiple phantoms (probably need to store some phantom id in the links for lookup)

def getSignatureFor(src_file, method):
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
    try:
        response = urllib.request.urlopen(url, timeout=3)
    except:
        sys.exit(2)

    result = response.read().decode('utf8');
    result = json.loads(result)['annotation_response'][0]

    for snippet in result.get('annotation', []):
      if not 'type' in snippet:
        continue
      if 'xref_signature' in snippet:
        signature = snippet['xref_signature']['signature']
        if '%s(' % method in signature:
          return signature

      elif 'internal_link' in snippet:
        signature = snippet['internal_link']['signature']
        if '::%s@chromium' % method in signature:
          return signature
    return ''

def getCallGraphFor(src_file, signature):
    url = ('https://cs.chromium.org/codesearch/json'
         '?call_graph_request=b'
         '&signature={signature}'
         '&file_spec=b'
         '&package_name=chromium'
         '&name={file_name}'
         '&file_spec=e'
         '&max_num_results=500'
         '&call_graph_request=e')
    url = url.format(signature=urllib.parse.quote(signature, safe=''), file_name=urllib.parse.quote(src_file, safe=''))

    try:
        response = urllib.request.urlopen(url, timeout=3)
    except:
        return []

    result = response.read().decode('utf8');
    result = json.loads(result)['call_graph_response'][0];
    node = result['node'];

    callers = [];
    last_signature = ''
    if not 'children' in node:
      return callers
    for child in node['children']:
      if child['signature'] == last_signature:
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

def getRefForMatch(filename, match):
  ref = {'filename': filename, 'line': match['line_number'], 'signature': match['signature']}
  if 'line_text' in match:
    ref['line_text'] = match['line_text']
  return ref;


def getXrefsFor(src_file, signature):
    url = ('https://cs.chromium.org/codesearch/json'
           '?xref_search_request=b'
           '&query={signature}'
           '&file_spec=b'
           '&package_name=chromium'
           '&name={file_name}'
           '&file_spec=e'
           '&max_num_results=500'
           '&xref_search_request=e')
    url = url.format(signature=urllib.parse.quote(signature, safe=''), file_name=urllib.parse.quote(src_file, safe=''))
    try:
        response = urllib.request.urlopen(url, timeout=3)
    except:
        sys.exit(2)

    result = response.read().decode('utf8');
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
                xrefs['definition'] = getRefForMatch(filename, match);
            elif match['type'] == 'HAS_DECLARATION':
                xrefs['declaration'] = getRefForMatch(filename, match);
            elif match['type'] == 'OVERRIDDEN_BY':
                xrefs.setdefault('overrides', []);
                xrefs['overrides'].append(getRefForMatch(filename, match));
            elif match['type'] == 'REFERENCED_AT':
                xrefs.setdefault('references', []);
                xrefs['references'].append(getRefForMatch(filename, match));
    return xrefs

def getRefsFor(src_file, signature):
    url = ('https://cs.chromium.org/codesearch/json'
           '?xref_search_request=b'
           '&query={signature}'
           '&file_spec=b'
           '&package_name=chromium'
           '&name={file_name}'
           '&file_spec=e'
           '&max_num_results=500'
           '&xref_search_request=e')
    url = url.format(signature=urllib.parse.quote(signature, safe=''), file_name=urllib.parse.quote(src_file, safe=''))
    try:
        response = urllib.request.urlopen(url, timeout=3)
    except:
        sys.exit(2)

    result = response.read().decode('utf8');
    result = json.loads(result)['xref_search_response'][0]
    status = result['status']
    if not 'search_result' in result:
        sys.exit(2)
    search_results = result['search_result']

    output = []

    for file_result in search_results:
        filename = file_result['file']['name']
        for match in file_result['match']:
            if not (match['type'] == 'REFERENCED_AT'):
                continue

            line = match['line_number']
            text = match['line_text']
            output.append({'filename': filename, 'line': line, 'text': text})

    return output

def getWord(cmd):
  for region in cmd.view.sel():
    if region.empty():
        # if we have no selection grab the current word
        word = cmd.view.word(region)
        if not word.empty():
            return cmd.view.substr(word)

def posixPath(path):
  if os.path.sep == '\\':
    return path.replace('\\','/');
  return path;

def getRoot(cmd, path):
  return 'src' + path.split('src')[1]

def goToLocation(cmd, src_path, caller):
  line = caller['line'];
  path = src_path + caller['filename']
  cmd.view.window().open_file(path + ":%d:0" % line, sublime.ENCODED_POSITION)

def goToSelection(cmd, src_path, callers, sel):
  if sel < 0:
    return
  goToLocation(cmd, src_path, callers[sel])

class ChromiumXRefsPopupCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    function_name = getWord(self);
    file_path = getRoot(self, self.view.file_name());
    file_path = posixPath(file_path);
    src_path = self.view.file_name().split('src')[0]
    src_path = posixPath(src_path)

    signature = getSignatureFor(file_path, function_name);
    if not signature:
      sys.exit(2);
    callers = getCallGraphFor(file_path, signature);

    items = []
    for caller in callers:
      items.append("%s: %s" % (caller['display_name'], caller['text']));
    if items:
      self.view.show_popup_menu(items, lambda x: goToSelection(self, src_path, callers, x));

class ChromiumXRefsCommand(sublime_plugin.TextCommand):
  def createPhantom(self, doc):
    loc = self.view.line(self.view.sel()[0]);
    return sublime.Phantom(loc, doc, sublime.LAYOUT_BELOW, lambda link: self.processLink(link, self.callers));

  def processLink(self, link, callers):
    link_type = link.split(':')[0]

    if link_type == 'declared':
      goToLocation(self, self.src_path, self.xrefs['declaration']);
      return;

    if link_type == 'defined':
      goToLocation(self, self.src_path, self.xrefs['definition']);
      return;

    if link_type == 'ref':
      ref = {}
      ref['line'] = int(link.split(':')[1])
      ref['filename'] = html.parser.HTMLParser().unescape(''.join(link.split(':')[2:]));
      goToLocation(self, self.src_path, ref);
      return;

    if link_type == 'filter':
      if link.split(':')[1] == 'test':
        self.show_tests = False;
        doc = self.genHtml();
        self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);
        return;

    if link_type == 'nofilter':
      if link.split(':')[1] == 'test':
        self.show_tests = True;
        doc = self.genHtml()
        self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);
        return;

    if link_type == 'killPhantom':
      self.view.chromium_x_refs_phantoms.update([])
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
      self.view.hide_popup();
      goToLocation(self, self.src_path, caller);
    elif (link_type == 'expand'):
      caller['callers'] = getCallGraphFor(self.file_path, caller['calling_signature'])
      doc = self.genHtml()
      self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);

    elif (link_type == 'shrink'):
      caller.pop('callers')
      doc = self.genHtml()
      self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);

    elif (link_type == 'filter'):

      caller.pop('callers')
      doc = self.genHtml()
      self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);



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
        link_expander = "<a id=expander href=shrink:" + str_loc + '>-</a>'
      else:
        link_expander = "<a id=expander href=expand:" + str_loc + '>+</a>'

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
    <style>
    body {
      background-color: beige;
    }
    * {
      font-size: 12px;
    }
    #expander {
      color: red;
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
    #filter {
      color: red;
    }
    </style>
    """

    tab = '&nbsp;' * 4;
    body += "<div class=navbar>";
    xrefs = self.xrefs;

    body += '<b>' + self.function_name + ':</b>' + tab
    if 'declaration' in xrefs:
      body += '<a href=declared:>Declaration</a>' + tab
    if 'definition' in xrefs:
      body += '<a href=defined:>Definition</a>'

    body += tab;

    if self.show_tests:
      body += '<a href=filter:test>[-Tests]</a>'
    else:
      body += '<a href=nofilter:test>[+Tests]</a>'

    body += tab
    body += '<a href=killPhantom>[X]</a>'
    body += "</div>"

    if self.callers:
      body += '<p><b>Callers:</b><br>'
      body += self.genHtmlImpl(self.callers, [])
      body += '</p>'


    if 'references' in xrefs:
      body += '<p><b>References:</b><br><ul>'

      last_file = ''
      for ref in xrefs['references']:
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

    return body

  def run(self, edit):
    self.show_tests = True;
    self.function_name = getWord(self);
    self.file_path = getRoot(self, self.view.file_name());
    self.src_path = self.view.file_name().split('src')[0]
    self.file_path = posixPath(self.file_path);
    self.src_path = posixPath(self.src_path);

    self.signature = getSignatureFor(self.file_path, self.function_name);
    if not self.signature:
      print("Could not find signature for: " + self.function_name);
      return;

    self.xrefs = getXrefsFor(self.file_path, self.signature);
    if not self.xrefs:
      print("Could not find xrefs for: " + self.function_name);
      return;

    self.callers = getCallGraphFor(self.file_path, self.signature);

    doc = self.genHtml();

    global gLastChromeCmd
    gLastChromeCmd = self;

    if not hasattr(self.view, 'chromium_x_refs_phantoms'):
      self.view.chromium_x_refs_phantoms = sublime.PhantomSet(self.view, "chromium_x_refs_phantoms");

    self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);

  def recall(self):
    doc = self.genHtml();
    if not hasattr(self.view, 'chromium_x_refs_phantoms'):
      self.view.chromium_x_refs_phantoms = sublime.PhantomSet(self.view, "chromium_x_refs_phantoms");

    self.view.chromium_x_refs_phantoms.update([self.createPhantom(doc)]);

class ChromiumRecallXRefsCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    global gLastChromeCmd
    if gLastChromeCmd:
      gLastChromeCmd.view = self.view;
      gLastChromeCmd.recall();

