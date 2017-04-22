# Copyright 2017 Josh Karlin. All rights reserved.
# Use of this source code is governed by the Apache license found in the LICENSE
# file.

import html
import html.parser
import os.path
import sys

import sublime, sublime_plugin

sys.path.append(os.path.join(os.path.dirname(__file__), "lib"))
import chromium_code_search as cs


# TODO store the phantom's location so that update calls can use the same location
# TODO support multiple phantoms (probably need to store some phantom id in the links for lookup)


g_last_xref_cmd = None  # The last chromium cmd that ran

cs.cacheResponses(True);

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

def goToLocation(cmd, src_path, caller):
  line = caller['line'];
  path = src_path + caller['filename']
  cmd.view.window().open_file(path + ":%d:0" % line, sublime.ENCODED_POSITION)

def goToSelection(cmd, src_path, callers, sel):
  if sel < 0:
    return
  goToLocation(cmd, src_path, callers[sel])

class ChromiumXrefsCommand(sublime_plugin.TextCommand):
  def __init__(self, view):
    self.view = view;
    self.data = {}

  def getWord(self):
    for region in self.view.sel():
      if region.empty():
          # if we have no selection grab the current word
          word = self.view.word(region)
          if not word.empty():
              self.selection_line = self.view.rowcol(region.a)[0]+1;
              return self.view.substr(word)

  def createPhantom(self, doc):
    xref_data = self.data[self.view.window().id()];
    loc = sublime.Region(0,0);
    return sublime.Phantom(loc, doc, sublime.LAYOUT_BELOW, lambda link: self.processLink(link, self.callers));

  def updatePhantom(self, phantom):

    xref_data = self.data[self.view.window().id()];
    xref_data['phantom_set'].update([phantom])

  def destroyPhantom(self):
    xref_data = self.data[self.view.window().id()];
    xref_data['phantom_set'].update([])
    self.view.window().run_command("hide_panel", {"panel": "output.chromium_x_refs"})

  def processLink(self, link, callers):
    link_type = link.split(':')[0]

    if link_type == 'selected_word':
      goToLocation(self, self.src_path, self.selection_ref);

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
        self.updatePhantom(self.createPhantom(doc));
        return;

    if link_type == 'nofilter':
      if link.split(':')[1] == 'test':
        self.show_tests = True;
        doc = self.genHtml()
        self.updatePhantom(self.createPhantom(doc));
        return;

    if link_type == 'killPhantom':
      self.destroyPhantom();
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
      goToLocation(self, self.src_path, caller);
    elif (link_type == 'expand'):
      caller['callers'] = cs.getCallGraphFor(caller['calling_signature'])
      doc = self.genHtml()
      self.updatePhantom(self.createPhantom(doc));

    elif (link_type == 'shrink'):
      caller.pop('callers')
      doc = self.genHtml()
      self.updatePhantom(self.createPhantom(doc));

    elif (link_type == 'filter'):
      caller.pop('callers')
      doc = self.genHtml()
      self.updatePhantom(self.createPhantom(doc));

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

    body += '<b> <a href=selected_word>' + self.selected_word + '</a>:</b>' + tab
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

  def getSignatureForSelection(self, edit):
    self.selected_word = self.getWord();
    self.file_path = getRoot(self, self.view.file_name());
    if self.file_path == '':
      self.log("Could not find src/ directory in path");
      return '';
    self.src_path = posixPath(self.view.file_name().split(self.file_path)[0]);
    self.file_path = posixPath(self.file_path)

    self.selection_ref = {'line': self.selection_line, 'filename': self.file_path}

    self.signature = cs.getSignatureFor(self.file_path, self.selected_word);
    return self.signature

  def log(self, msg):
      print(msg);
      self.view.window().status_message(msg);

  def initWindow(self, window):
    if not window.id() in self.data:
      self.data[window.id()] = {}
      xref_data = self.data[window.id()];
      window.destroy_output_panel("chromium_x_refs");
      xref_data['panel'] = window.create_output_panel("chromium_x_refs", False);
      xref_data['phantom_set'] = sublime.PhantomSet(xref_data['panel'], "phantoms");


  def run(self, edit):
    self.show_tests = True;

    if not self.getSignatureForSelection(edit):
      self.log("Could not find signature for: " + self.selected_word);
      return;

    self.xrefs = cs.getXrefsFor(self.signature);
    if not self.xrefs:
      self.log("Could not find xrefs for: " + self.selected_word);
      return;

    self.callers = cs.getCallGraphFor(self.signature);

    doc = self.genHtml();

    window = self.view.window();
    self.initWindow(window);

    global g_last_xref_cmd
    g_last_xref_cmd = self;

    self.updatePhantom(self.createPhantom(doc));
    window.run_command("show_panel", {"panel": "output.chromium_x_refs"})


  def recall(self):
    window = self.view.window();
    self.initWindow(window);
    doc = self.genHtml();

    self.updatePhantom(self.createPhantom(doc));
    window = self.view.window();
    window.run_command("show_panel", {"panel": "output.chromium_x_refs"})

class ChromiumRecallXrefsCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    global g_last_xref_cmd
    if g_last_xref_cmd:
      g_last_xref_cmd.recall();

