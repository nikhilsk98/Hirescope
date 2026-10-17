-- HireScope Launcher: receives hirescope:// links from the dashboard and hands each one, as a single quoted
-- argument, to scripts/launcher.sh (Python validates it; nothing here interprets the link). It runs in the
-- background: no window, no Dock icon (LSUIElement), no Terminal. A stay-open applet: links that arrive while one is
-- being handled wait in its event queue (in order) instead of being lost; it quits after two idle minutes.
-- Built by scripts/install_launcher.sh, which fills in the repository path below.
global idleRounds

on open location theURL
	set idleRounds to 0
	try
		do shell script "/bin/zsh " & quoted form of "{{REPO}}/scripts/launcher.sh" & " " & quoted form of theURL
	on error errMsg
		display notification errMsg with title "HireScope Launcher"
	end try
end open location

on idle
	try
		set idleRounds to idleRounds + 1
	on error
		set idleRounds to 1
	end try
	if idleRounds > 4 then quit
	return 30
end idle

on run
	display dialog "HireScope Launcher opens hirescope:// links from the HireScope dashboard (Generate Resume, Bucket, Resume Builder, Mark Applied) in the background. There is nothing to do here." buttons {"OK"} default button "OK" with title "HireScope Launcher"
end run
