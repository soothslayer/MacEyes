tell application "System Events"
    tell process "Python"
        click menu bar item 1 of menu bar 2
        delay 0.5
        click menu item "Voice Action" of menu 1 of menu bar item 1 of menu bar 2
    end tell
end tell
