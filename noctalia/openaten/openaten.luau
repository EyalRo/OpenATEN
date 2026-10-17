-- OpenATEN bar widget: shows bridge/forwarding state from the daemon's status file.
-- Click toggles forwarding. Start the daemon with `openaten daemon` (see the OpenATEN repo).

local STALE_SECONDS = 6
local ctl = noctalia.expandPath("~/.local/bin/openaten") .. " ctl"  -- install: nix build .#default -o ~/.local/share/openaten-pkg && ln -sfn ~/.local/share/openaten-pkg/bin/openaten ~/.local/bin/openaten
local runtime = noctalia.getenv("XDG_RUNTIME_DIR") or "/tmp"
local statusFile = runtime .. "/openaten/status.json"

local function render(st)
    if st == nil or os.time() - (st.ts or 0) > STALE_SECONDS then
        barWidget.setGlyph("keyboard-off")
        barWidget.setGlyphColor("on_surface_variant")
        barWidget.setTooltip("OpenATEN daemon not running")
        return
    end
    if st.error ~= nil and st.error ~= "" then
        barWidget.setGlyph("alert-triangle")
        barWidget.setGlyphColor("error")
        barWidget.setTooltip(st.error)
    elseif st.forwarding then
        barWidget.setGlyph("keyboard")
        barWidget.setGlyphColor("primary")
        barWidget.setTooltip("Forwarding input to the other computer (" .. (st.hotkey or "") .. " toggles)")
    elseif st.bridge == "present" then
        barWidget.setGlyph("keyboard")
        barWidget.setGlyphColor("on_surface")
        barWidget.setTooltip("Bridge connected, forwarding off - click to forward")
    else
        barWidget.setGlyph("keyboard-off")
        barWidget.setGlyphColor("on_surface_variant")
        barWidget.setTooltip("No bridge: connect both USB-C cables to live computers")
    end
end

function update()
    local text = noctalia.readFile(statusFile)
    local st = nil
    if text ~= nil then
        local ok, decoded = pcall(noctalia.json.decode, text)
        if ok and type(decoded) == "table" then st = decoded end
    end
    render(st)
end

function onClick()
    noctalia.runAsync(ctl .. " toggle", function(res)
        if res.exitCode ~= 0 then
            noctalia.notify("OpenATEN", "Could not reach the daemon (is `openaten daemon` running?)")
        end
        update()
    end)
end

noctalia.setUpdateInterval(1000)
update()
