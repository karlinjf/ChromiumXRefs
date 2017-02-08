# Chromium Utilities Plugin for Sublime Text
### Show call hierarchy for a given function
- **Usage**: Ctrl+Alt+Left mouse click (or ctrl+alt+c) on a function in the
  C++ Chromium codebase.
- **Function**: Inserts a frame in the code with the callers of the given
  function on the line below the cursor. Click on the caller to jump to the
  code. Click on the "+" sign next to the caller to delve deeper in the call
  stack. Click on the "-tests" button on the top to remove functions with
  "test" in their name. Click on the "X" button in the top right to close the
  frame.
- **Notes**: The hierarchy is retrieved from cs.chromium.org and the data will
  only be as recent as what that site last indexed. This means local changes
  you have made to the codebase will not be reflected.

### Recall call hierarchy for a given function
- **Usage**: Ctrl+Alt+Right mouse click (or ctrl+alt+r) anywhere in C++ code.
- **Function**: Recalls the last hierarchy displayed and inserts it below the
  cursor's position. You don't have to be in the same source file you were in
  when you originally displayed the hierarchy. This is useful when exploring
  through a call stack and you need to recall the last hierarchy that you
  displayed.
