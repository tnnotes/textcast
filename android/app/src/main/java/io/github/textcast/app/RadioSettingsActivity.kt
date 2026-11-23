package io.github.textcast.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import io.github.textcast.R
import io.github.textcast.core.AirProfile
import io.github.textcast.core.ConfigError
import io.github.textcast.core.Hardware
import io.github.textcast.core.HardwareError
import io.github.textcast.core.Protocol
import io.github.textcast.core.Regions
import io.github.textcast.core.Settings
import io.github.textcast.core.asMap
import io.github.textcast.core.fmtG
import java.io.File
import java.util.Locale

/**
 * Radio settings: module, region preset and manual values, log size.
 * Same choices as the desktop Radio settings (F7).
 */
class RadioSettingsActivity : Activity() {
    private lateinit var module: Spinner
    private lateinit var moduleHint: TextView
    private lateinit var region: Spinner
    private lateinit var freq: EditText
    private lateinit var sf: Spinner
    private lateinit var bw: Spinner
    private lateinit var cr: Spinner
    private lateinit var power: EditText
    private lateinit var preamble: EditText
    private lateinit var dwell: EditText
    private lateinit var duty: EditText
    private lateinit var logRows: EditText
    private lateinit var logDays: EditText
    private lateinit var preview: TextView
    private lateinit var regionNote: TextView

    private var modules: List<Pair<String, String>> = emptyList()
    private val regions = Regions.PRESETS.keys.toList() + listOf("custom")
    private val sfs = (5..12).toList()
    private val bws = Regions.BW_CODES.keys.sortedDescending()
    private var filling = false
    private var regionPos = -1

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.radio_settings)
        val box = column().apply { setPadding(dp(20), dp(8), dp(20), dp(24)) }
        fun section(t: String) = box.addView(TextView(this).apply {
            text = t; textSize = 16f; setTextColor(color(R.color.accent)); setPadding(0, dp(16), 0, dp(4))
        })
        fun row(title: String, v: View) {
            box.addView(label(title))
            box.addView(v)
        }
        fun number(decimal: Boolean = false, signed: Boolean = false) = EditText(this).apply {
            setSingleLine(true)
            inputType = InputType.TYPE_CLASS_NUMBER or (if (decimal) InputType.TYPE_NUMBER_FLAG_DECIMAL else 0) or
                (if (signed) InputType.TYPE_NUMBER_FLAG_SIGNED else 0)
        }

        section("Module")
        module = Spinner(this); row("Radio module", module)
        moduleHint = label(""); box.addView(moduleHint)
        box.addView(Button(this, null, android.R.attr.borderlessButtonStyle).apply {
            text = "Import a module profile (.json)…"
            setOnClickListener { pickProfile() }
        })

        section("Preset")
        region = Spinner(this); row("Region", region)
        regionNote = label(""); box.addView(regionNote)

        section("Radio (every node in a network must match)")
        freq = number(decimal = true); row("Frequency (MHz)", freq)
        sf = Spinner(this); row("Spreading factor", sf)
        bw = Spinner(this); row("Bandwidth", bw)
        cr = Spinner(this); row("Coding rate", cr)
        power = number(); row("TX power (dBm)", power)
        preamble = number(); row("Preamble (symbols)", preamble)

        section("Limits")
        dwell = EditText(this).apply { setSingleLine(true) }; row("Max dwell time per packet (ms, or none)", dwell)
        duty = EditText(this).apply { setSingleLine(true) }; row("Duty cycle (% per hour, or none)", duty)
        logRows = number(); row("Log: keep at most N messages (0 = all)", logRows)
        logDays = number(decimal = true); row("Log: delete messages older than N days (0 = never)", logDays)

        preview = TextView(this).apply { textSize = 13f; setPadding(0, dp(16), 0, dp(8)) }
        box.addView(preview)

        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons.addView(Button(this, null, android.R.attr.borderlessButtonStyle).apply {
            text = getString(R.string.cancel); setOnClickListener { cancel() }
        })
        buttons.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f))
        buttons.addView(Button(this).apply { text = getString(R.string.apply); setOnClickListener { apply() } })
        box.addView(buttons)

        setContentView(ScrollView(this).apply { addView(box) })

        sf.adapter = adapter(sfs.map { "SF$it" })
        bw.adapter = adapter(bws.map { "${fmtG(it / 1000.0)} kHz" })
        cr.adapter = adapter((1..4).map { "4/${it + 4}" })
        region.adapter = adapter(regions)
        refreshModules(session.hwId)

        fill(Settings.profileFields(Protocol.profile))
        val log = session.config()["log"].asMap()
        logRows.setText(((log["max_rows"] as? Number)?.toLong() ?: 20000L).toString())
        logDays.setText(fmtG((log["max_days"] as? Number)?.toDouble() ?: 30.0))

        val watcher = object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) { if (!filling) updatePreview() }
        }
        listOf(freq, power, preamble, dwell, duty).forEach { it.addTextChangedListener(watcher) }
        val sel = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                if (filling) return
                when (p) {
                    region -> if (pos != regionPos) {       // the user picked another preset
                        regionPos = pos
                        regions[pos].takeIf { it != "custom" }?.let { r ->
                            fill(Settings.profileFields(Settings.buildProfile(mapOf("region" to r))))
                        }
                    }
                    module -> updateModule()
                }
                updatePreview()
            }
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
        listOf(module, region, sf, bw, cr).forEach { it.onItemSelectedListener = sel }
        updateModule()
        updatePreview()
    }

    private fun adapter(items: List<String>) = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, items)

    private fun refreshModules(select: String) {
        modules = session.hardwareChoices()
        module.adapter = adapter(modules.map { (id, desc) -> if (File(id).isAbsolute) "${File(id).nameWithoutExtension} — $desc" else "$id — $desc" })
        module.setSelection(modules.indexOfFirst { it.first == select }.coerceAtLeast(0))
    }

    private fun fill(f: Map<String, Any?>) {
        filling = true
        val r = f["region"] as? String ?: Regions.DEFAULT_REGION
        regionPos = regions.indexOf(r).coerceAtLeast(0)
        region.setSelection(regionPos)
        regionNote.text = Regions.PRESETS[r]?.note ?: "Manual settings"
        freq.setText(String.format(Locale.ROOT, "%.3f", (f["freq_mhz"] as Number).toDouble()))
        sf.setSelection(sfs.indexOf((f["sf"] as Number).toInt()).coerceAtLeast(0))
        bw.setSelection(bws.indexOf(Math.round((f["bw_khz"] as Number).toDouble() * 1000).toInt()).coerceAtLeast(0))
        cr.setSelection(((f["cr"] as Number).toInt() - 1).coerceIn(0, 3))
        power.setText((f["power_dbm"] as Number).toString())
        preamble.setText((f["preamble"] as Number).toString())
        dwell.setText((f["max_dwell_ms"] as? Number)?.let { fmtG(it.toDouble()) } ?: "none")
        duty.setText((f["duty_cycle"] as? Number)?.let { fmtG(it.toDouble() * 100) } ?: "none")
        filling = false
    }

    private fun selectedModule(): String = modules.getOrNull(module.selectedItemPosition)?.first ?: Hardware.DEFAULT

    private fun updateModule() {
        val hw = session.loadHardware(selectedModule())
        // preview the payload limits of the chosen module (restored on Cancel)
        Protocol.setHardwareLimits(hw.maxPayload.ifEmpty { mapOf("*" to 255) }, hw.firmwareOverhead)
        var text = hw.description + ". " + if (hw.verified) "Tested with TextCast." else "Untested with TextCast. ${hw.notes}"
        if (session.node?.radio != null && selectedModule() != session.hwId) text += "\nA module change takes effect at the next start."
        moduleHint.text = text
        moduleHint.setTextColor(color(if (hw.verified) R.color.text_meta else R.color.text_warn))
    }

    private fun radioFields(): Map<String, Any?> {
        fun off(s: String): Any? = s.trim().lowercase().let { if (it.isEmpty() || it == "none" || it == "off" || it == "0") "none" else it }
        val dutyPct = off(duty.text.toString())
        return linkedMapOf(
            "region" to regions[region.selectedItemPosition],
            "freq_mhz" to freq.text.toString().trim(),
            "sf" to sfs[sf.selectedItemPosition].toLong(),
            "bw_khz" to bws[bw.selectedItemPosition] / 1000.0,
            "cr" to (cr.selectedItemPosition + 1).toLong(),
            "power_dbm" to power.text.toString().trim(),
            "preamble" to preamble.text.toString().trim(),
            "max_dwell_ms" to off(dwell.text.toString()),
            "duty_cycle" to if (dutyPct == "none") "none" else
                (dutyPct as String).toDoubleOrNull()?.let { it / 100.0 } ?: dutyPct)
    }

    /** Numbers typed as text become numbers, as in the desktop config file. */
    private fun normalized(r: Map<String, Any?>): MutableMap<String, Any?> = r.mapValues { (_, v) ->
        if (v is String) v.toLongOrNull() ?: v.toDoubleOrNull() ?: v else v
    }.toMutableMap()

    private fun build(): AirProfile = Settings.buildProfile(radioFields())

    private fun updatePreview(): AirProfile? = try {
        val p = build()
        preview.text = Settings.describe(p)
        preview.setTextColor(color(R.color.text_meta))
        regionNote.text = Regions.PRESETS[p.name.trimEnd('*')]?.note ?: "Manual settings"
        p
    } catch (e: ConfigError) {
        preview.text = e.text(); preview.setTextColor(color(R.color.text_error)); null
    } catch (e: Exception) {
        preview.text = "Check the values: ${e.text()}"; preview.setTextColor(color(R.color.text_error)); null
    }

    private fun restoreLimits() {
        val hw = session.hw
        Protocol.setHardwareLimits(hw.maxPayload.ifEmpty { mapOf("*" to 255) }, hw.firmwareOverhead)
    }

    private fun cancel() { restoreLimits(); finish() }

    @Deprecated("Activity API")
    override fun onBackPressed() { restoreLimits(); super.onBackPressed() }

    private fun apply() {
        val p = updatePreview() ?: return
        val newHw = selectedModule()
        val rows = logRows.text.toString().toLongOrNull() ?: 20000L
        val days = logDays.text.toString().toDoubleOrNull() ?: 30.0
        restoreLimits()
        val cfg = session.config()
        cfg["radio"] = normalized(radioFields())
        cfg["log"] = linkedMapOf<String, Any?>("max_rows" to rows, "max_days" to days)
        cfg["hardware"] = linkedMapOf<String, Any?>("type" to newHw)
        try { session.saveConfig(cfg) } catch (e: Exception) { toast("Cannot save: ${e.text()}"); return }
        val n = session.node
        n?.logMaxRows = rows.toInt(); n?.logMaxDays = days
        val connected = n?.radio != null
        if (!connected && newHw != session.hwId) session.activateHardware(newHw)
        if (connected && newHw != session.hwId) session.addSystem("radio module set to ${File(newHw).name} - it takes effect at the next start")
        if (n != null && connected) {
            preview.text = "Applying to the radio…"
            bg({ n.reconfigure(p) }) { r ->
                r.onSuccess { problems ->
                    if (problems.isEmpty()) session.addSystem("radio settings applied: ${p.name}")
                    else session.addSystem("radio settings applied with problems: ${problems.joinToString("; ")}")
                    finish()
                }.onFailure { e -> preview.text = e.text(); preview.setTextColor(color(R.color.text_error)) }
            }
        } else {
            val again = try { Settings.buildProfile(cfg["radio"].asMap()) } catch (e: ConfigError) { p }
            Protocol.applyProfile(again)
            finish()
        }
    }

    // -- custom module profiles -------------------------------------------------------------

    private fun pickProfile() {
        val i = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
        startActivityForResult(i, REQ_PROFILE)
    }

    @Deprecated("Activity API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_PROFILE || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        try {
            val text = contentResolver.openInputStream(uri)!!.use { String(it.readBytes(), Charsets.UTF_8) }
            val tmp = File(cacheDir, "profile.json").apply { writeText(text) }
            val hw = Hardware.loadFile(tmp)                    // validates
            val dest = File(session.hardwareDir, hw.name.replace(Regex("[^A-Za-z0-9_.-]"), "_") + ".json")
            tmp.copyTo(dest, overwrite = true)
            refreshModules(dest.path)
            updateModule(); updatePreview()
            toast("Module profile ${hw.name} added")
        } catch (e: HardwareError) {
            info("Not a valid module profile", e.text())
        } catch (e: Exception) {
            toast("Cannot read the file: ${e.text()}")
        }
    }

    companion object { private const val REQ_PROFILE = 30 }
}
