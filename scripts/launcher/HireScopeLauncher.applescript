-- HireScope Launcher: receives hirescope:// links from the dashboard and hands each one, as a single quoted
-- argument, to scripts/launcher.sh (Python validates it; nothing here interprets the link). It runs in the
-- background: no window, no Dock icon (LSUIElement), no Terminal. Built by scripts/install_launcher.sh, which
-- fills in the repository path below.
on open location theURL
	try
		do shell script "/bin/zsh " & quoted form of "{{REPO}}/scripts/launcher.sh" & " " & quoted form of theURL
	on error errMsg
		display notification errMsg with title "HireScope Launcher"
	end try
end open location

on run
	display dialog "HireScope Launcher opens hirescope:// links from the HireScope dashboard (Generate Resume, Bucket, Resume Builder, Mark Applied) in the background. There is nothing to do here." buttons {"OK"} default button "OK" with title "HireScope Launcher"
end run
