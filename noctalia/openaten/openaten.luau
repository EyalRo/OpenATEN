-- OpenATEN bar widget: shows bridge/forwarding state from the daemon's status file.
-- Click toggles forwarding. Start the daemon with `openaten daemon` (see the OpenATEN repo).
-- Setting `openaten_path` points at the openaten binary (default ~/.local/bin/openaten); install it with
--   nix build .#default -o ~/.local/share/openaten-pkg && ln -sfn ~/.local/share/openaten-pkg/bin/openaten ~/.local/bin/openaten

local STALE_SECONDS = 6
local runtime = noctalia.getenv("XDG_RUNTIME_DIR") or "/tmp"
local statusFile = runtime .. "/openaten/status.json"

local function shellQuote(s)
    return "'" .. string.gsub(s, "'", "'\\''") .. "'"
end

local function ctlCommand()
    local path = noctalia.getConfig("openaten_path")
    if path == nil or path == "" then path = "~/.local/bin/openaten" end
    return shellQuote(noctalia.expandPath(path)) .. " ctl"
end

local function nonEmpty(v)
    return v ~= nil and v ~= ""
end

-- Tooltip rows: only fields the daemon actually reported (older daemons lack role/edges/transfers).
local function rows(headline, st)
    local out = { { "State", headline } }
    local port = st["end"]  -- `end` is a Lua keyword: the daemon's USB port path of the claimed end, when it holds one
    local bridge = "not found"
    if st.bridge == "present" then bridge = nonEmpty(port) and ("connected, port " .. port) or "connected" end
    out[#out + 1] = { "Bridge", bridge }
    if nonEmpty(st.role) then out[#out + 1] = { "Role", st.role } end
    if type(st.devices) == "table" and #st.devices > 0 then out[#out + 1] = { "Input devices", tostring(#st.devices) } end
    if nonEmpty(st.hotkey) then out[#out + 1] = { "Hotkey", st.hotkey } end
    if nonEmpty(st.edges) and st.edges ~= "off" then out[#out + 1] = { "Edge hand-off", st.edges } end
    if type(st.transfers) == "table" and #st.transfers > 0 then out[#out + 1] = { "Transfers", tostring(#st.transfers) .. " active" } end
    if nonEmpty(st.error) then out[#out + 1] = { "Error", st.error } end
    return out
end

local function render(st)
    if st == nil or os.time() - (st.ts or 0) > STALE_SECONDS then
        barWidget.setGlyph("keyboard-off")
        barWidget.setGlyphColor("on_surface_variant")
        barWidget.setTooltip({ { "State", "daemon not running" }, { "Start", "openaten daemon" } })
        return
    end
    if nonEmpty(st.error) then
        barWidget.setGlyph("alert-triangle")
        barWidget.setGlyphColor("error")
        barWidget.setTooltip(rows("error", st))
    elseif st.forwarding then
        barWidget.setGlyph("keyboard")
        barWidget.setGlyphColor("primary")
        barWidget.setTooltip(rows("forwarding to the other computer", st))
    elseif st.bridge == "present" then
        barWidget.setGlyph("keyboard")
        barWidget.setGlyphColor("on_surface")
        barWidget.setTooltip(rows("connected, forwarding off (click to forward)", st))
    else
        barWidget.setGlyph("keyboard-off")
        barWidget.setGlyphColor("on_surface_variant")
        barWidget.setTooltip(rows("no bridge: connect both USB-C cables to live computers", st))
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
    noctalia.runAsync(ctlCommand() .. " toggle", function(res)
        if res.exitCode ~= 0 then
            noctalia.notify("OpenATEN", "Could not reach the daemon (is `openaten daemon` running?)")
        end
        update()
    end)
end

noctalia.setUpdateInterval(1000)
update()
