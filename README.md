# Chromium X-Refs Plugin for Sublime Text

The ChromiumXRefs plugin shows cross-references for Chromium code using the codesearch backend. It displays the x-refs in a window at the bottom of the editor. These include usages, callers, overrides, declaration, and implementation. There is special sauce built into the call-graph, such that it correctly infers callers that posttask (including from other threads), ipcs (cross-process call-graphs!), and state machine transitions (for DoLoop state machines). This means that the call graph can dig from a cache state in the net/ state-machine all the way back to the blink loader!

 1. Methods that create callbacks are considered callers
 1. IPC calls are detected and the sender is considered the caller
 1. net/ DoLoop state transitions are detected and considered callers
 1. Mojo callers are under construction

![Screenshot](/media/chromium_x_refs.gif)

### Installation
Using Package Control, install the package named ChromiumXRefs.

### Show cross-references for a given function or variable
- **Command**: Chromium X-Refs
- **Usage**: Ctrl+Alt+Left mouse click (or ctrl+alt+\\) on most text in the
  C++ Chromium codebase. Use the command key instead of alt on OSX.
- **Function**: Inserts a frame in the code with links to declaration,
  definition, callers, and x-refs for the given text. Click on the caller to
  jump to the code. Click on the "+" sign next to the caller to delve deeper
  in the call stack. Click on the "-tests" button on the top to remove
  functions with "test" in their name. Click on the "X" button in the top
  right to close the frame.
- **Notes**: The data is retrieved from cs.chromium.org and the data will
  only be as recent as what that site last indexed. This means local changes
  you have made to the codebase will not be reflected.

### Recall last shown x-refs frame
- **Command**: Chromium Recall X-Refs
- **Usage**: Ctrl+Alt+Right mouse click (or ctrl+alt+shift+\\) anywhere in C++
  code. Use the command key instead of alt on OSX.
- **Function**: Recalls the last x-refs frame displayed and inserts it below
  the cursor's position. You don't have to be in the same source file you were
  in when you originally displayed the hierarchy. This is useful when
  exploring through a call stack and you need to recall the last hierarchy
  that you displayed.


### Suggested mouse mapping
- A mouse mapping is quite useful for this plugin. Paste the following in your
  "Default (OS).sublime-mousemap" file in your User/ directory. Replace "OS"
  with one of Windows, OSX, or Linux. Then you should be able to get x-refs
  with a ctrl+alt + left mouse click.
```json
[
  {
    "button": "button1",
    "count": "1",
    "modifiers": ["ctrl", "alt"],
    "press_command": "drag_select",
    "command": "chromium_xrefs"
  },

  {
    "button": "button2",
    "count": "1",
    "modifiers": ["ctrl", "alt"],
    "press_command": "drag_select",
    "command": "chromium_recall_xrefs"
  }
]
```
