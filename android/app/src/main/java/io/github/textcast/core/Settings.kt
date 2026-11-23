package io.github.textcast.core

import java.io.File

/**
 * Region presets and settings (port of regions.py and config.py). The config
 * file has the same layout as the desktop config.json.
 *
 * Presets are NOT legal advice: check your regulator.
 */
object Regions {
    class Preset(val freqMhz: Double, val sf: Int, val bwHz: Int, val powerDbm: Int,
                 val maxDwellMs: Double?, val dutyCycle: Double?, val note: String)

    val PRESETS: LinkedHashMap<String, Preset> = linkedMapOf(
        "AS923-2" to Preset(920.800, 7, 125_000, 14, 400.0, 0.01,
            "Vietnam/Indonesia (AS923-2 band). 920.8 MHz sits outside the usual AS923-2 LoRaWAN gateway channels (921.4-922.8). Default."),
        "AS923-1" to Preset(922.000, 7, 125_000, 14, 400.0, 0.01,
            "Japan, Malaysia, Singapore, ... Japan requires listen-before-talk, which LA66 P2P firmware v1.2.4 does not do."),
        "AS923-3" to Preset(917.000, 7, 125_000, 14, 400.0, 0.01, "AS923-3 countries (e.g. Philippines)."),
        "AS923-4" to Preset(917.700, 7, 125_000, 14, 400.0, 0.01, "AS923-4 (Israel)."),
        "EU868" to Preset(869.525, 7, 125_000, 14, null, 0.10,
            "Europe, sub-band 869.4-869.65 MHz (10 % duty cycle). Needs the 868 MHz LA66 variant."),
        "US915" to Preset(915.000, 7, 500_000, 20, 400.0, null,
            "USA. 500 kHz bandwidth (wideband digital modulation). Check FCC Part 15.247 for your use."),
        "AU915" to Preset(917.000, 7, 125_000, 20, 400.0, null, "Australia / New Zealand (915-928 MHz)."),
        "IN865" to Preset(866.000, 7, 125_000, 20, null, null, "India (865-867 MHz). Needs the 868 MHz LA66 variant."),
        "KR920" to Preset(923.000, 7, 125_000, 14, null, 0.01,
            "South Korea. Listen-before-talk is required, which LA66 P2P firmware v1.2.4 does not do."),
    )
    const val DEFAULT_REGION = "AS923-2"

    /** LA66 P2P bandwidth codes (AT+BW). */
    val BW_CODES: LinkedHashMap<Int, Int> = linkedMapOf(125_000 to 0, 250_000 to 1, 500_000 to 2, 62_500 to 3,
        41_670 to 4, 31_250 to 5, 20_830 to 6, 15_630 to 7, 10_420 to 8, 7_810 to 9)

    fun bwFromKhz(khz: Double): Int {
        val r = Math.round(khz * 100) / 100.0
        return BW_CODES.keys.firstOrNull { Math.round(it / 10.0) / 100.0 == r }
            ?: throw ConfigError("bandwidth must be one of ${BW_CODES.keys.map { fmtG(it / 1000.0) }.sorted()} kHz")
    }

    fun apply(p: AirProfile, region: String): AirProfile {
        val pr = PRESETS[region] ?: PRESETS[DEFAULT_REGION]!!
        p.name = region; p.freqMhz = pr.freqMhz; p.sf = pr.sf; p.bwHz = pr.bwHz
        p.txPowerDbm = pr.powerDbm; p.maxDwellMs = pr.maxDwellMs; p.dutyCycle = pr.dutyCycle
        return p
    }
}

class ConfigError(msg: String) : IllegalArgumentException(msg)

/** Python's "%g"-style number text: 125.0 -> "125", 62.5 -> "62.5". */
fun fmtG(v: Double): String =
    if (v == Math.floor(v) && Math.abs(v) < 1e15) v.toLong().toString() else v.toString()

object Settings {
    const val MIN_PAYLOAD = 16

    fun defaults(): MutableMap<String, Any?> = linkedMapOf(
        "radio" to linkedMapOf<String, Any?>("region" to Regions.DEFAULT_REGION, "freq_mhz" to null, "sf" to null,
            "bw_khz" to null, "cr" to null, "power_dbm" to null, "preamble" to null,
            "max_dwell_ms" to "preset", "duty_cycle" to "preset"),
        "log" to linkedMapOf<String, Any?>("max_rows" to 20000L, "max_days" to 30L),
        "hardware" to linkedMapOf<String, Any?>("type" to Hardware.DEFAULT),
        "ui" to linkedMapOf<String, Any?>("port" to null, "nick" to null),
    )

    fun load(file: File): MutableMap<String, Any?> {
        val cfg = defaults()
        if (file.exists()) {
            val data = try { Json.parseObject(file.readText()) } catch (e: Exception) {
                throw ConfigError("${file.name} is not valid JSON: ${e.message}")
            }
            for ((section, v) in cfg) v.asMap().putAll(data[section].asMap())
        }
        return cfg
    }

    fun save(cfg: Map<String, Any?>, file: File) {
        val tmp = File(file.path + ".tmp")
        tmp.writeText(Json.stringify(cfg, 2))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }

    private fun given(radio: Map<String, Any?>, key: String): Boolean {
        val v = radio[key]
        return v != null && v != "preset"
    }

    private fun num(v: Any?): Double = when (v) {
        is Number -> v.toDouble()
        is String -> v.trim().toDoubleOrNull() ?: throw ConfigError("bad radio setting: '$v'")
        else -> throw ConfigError("bad radio setting: $v")
    }

    private fun offOrNum(v: Any?): Double? =
        if (v == 0L || v == 0.0 || v == 0 || v == "none" || v == "off") null else num(v)

    fun buildProfile(radio: Map<String, Any?>): AirProfile {
        val region = (radio["region"] as? String)?.takeIf { it.isNotEmpty() } ?: Regions.DEFAULT_REGION
        if (region !in Regions.PRESETS && region != "custom")
            throw ConfigError("unknown region '$region'. Choose one of: ${Regions.PRESETS.keys.joinToString()} or custom")
        val p = Regions.apply(AirProfile(), if (region == "custom") Regions.DEFAULT_REGION else region)
        p.name = region
        if (given(radio, "freq_mhz")) p.freqMhz = num(radio["freq_mhz"])
        if (given(radio, "sf")) p.sf = num(radio["sf"]).toInt()
        if (given(radio, "bw_khz")) p.bwHz = Regions.bwFromKhz(num(radio["bw_khz"]))
        if (given(radio, "cr")) p.cr = num(radio["cr"]).toInt()
        if (given(radio, "power_dbm")) p.txPowerDbm = num(radio["power_dbm"]).toInt()
        if (given(radio, "preamble")) p.preamble = num(radio["preamble"]).toInt()
        if (given(radio, "max_dwell_ms")) p.maxDwellMs = offOrNum(radio["max_dwell_ms"])
        if (given(radio, "duty_cycle")) p.dutyCycle = offOrNum(radio["duty_cycle"])
        if (region != "custom") {
            val ref = Regions.apply(AirProfile(), region)
            if (p.freqMhz != ref.freqMhz || p.sf != ref.sf || p.bwHz != ref.bwHz || p.cr != ref.cr ||
                p.txPowerDbm != ref.txPowerDbm || p.preamble != ref.preamble ||
                p.maxDwellMs != ref.maxDwellMs || p.dutyCycle != ref.dutyCycle) p.name = "$region*"
        }
        validate(p)
        p.maxPayload = Protocol.effectivePayload(p)
        if (p.maxPayload < MIN_PAYLOAD)
            throw ConfigError("SF${p.sf} at ${fmtG(p.bwHz / 1000.0)} kHz leaves only ${p.maxPayload} B per message " +
                "under the ${fmtG(p.maxDwellMs ?: 0.0)} ms dwell limit. Use a lower SF or a wider bandwidth, " +
                "or remove the dwell limit if your region allows it.")
        return p
    }

    fun validate(p: AirProfile) {
        if (p.freqMhz !in 137.0..1020.0) throw ConfigError("frequency must be 137-1020 MHz (SX1262 range)")
        if (p.sf !in 5..12) throw ConfigError("spreading factor must be 5-12")
        if (p.bwHz !in Regions.BW_CODES) throw ConfigError("bandwidth not supported")
        if (p.cr !in 1..4) throw ConfigError("coding rate must be 1-4 (4/5 .. 4/8)")
        if (p.txPowerDbm !in 0..22) throw ConfigError("TX power must be 0-22 dBm (LA66 range)")
        if (p.preamble !in 6..65535) throw ConfigError("preamble must be 6-65535 symbols")
        val dc = p.dutyCycle
        if (dc != null && !(dc > 0 && dc <= 1)) throw ConfigError("duty cycle must be between 0 and 1 (e.g. 0.01 = 1 %)")
    }

    /** Profile as the editable values shown in the settings screen. */
    fun profileFields(p: AirProfile): MutableMap<String, Any?> = linkedMapOf(
        "region" to p.name.trimEnd('*'), "freq_mhz" to p.freqMhz, "sf" to p.sf.toLong(),
        "bw_khz" to p.bwHz / 1000.0, "cr" to p.cr.toLong(), "power_dbm" to p.txPowerDbm.toLong(),
        "preamble" to p.preamble.toLong(),
        "max_dwell_ms" to (p.maxDwellMs?.takeIf { it > 0 } ?: "none"),
        "duty_cycle" to (p.dutyCycle?.takeIf { it > 0 } ?: "none"))

    fun describe(p: AirProfile): String {
        val dwell = p.maxDwellMs?.takeIf { it > 0 }?.let { "${fmtG(it)} ms" } ?: "none"
        val duty = p.dutyCycle?.takeIf { it > 0 }?.let { "${fmtG(it * 100)} %" } ?: "none"
        return "${String.format(java.util.Locale.ROOT, "%.3f", p.freqMhz)} MHz  SF${p.sf}  " +
            "BW ${fmtG(p.bwHz / 1000.0)} kHz  CR 4/${p.cr + 4}  ${p.txPowerDbm} dBm\n" +
            "max ${p.maxPayload} B per message (${Math.round(Protocol.airtimeMs(p.maxPayload, p))} ms on air), " +
            "dwell $dwell, duty cycle $duty"
    }

    /** Load a hardware profile and apply its payload limits to the active profile. */
    fun activateHardware(hw: HardwareProfile): HardwareProfile {
        Protocol.setHardwareLimits(hw.maxPayload.ifEmpty { mapOf("*" to 255) }, hw.firmwareOverhead)
        val p = Protocol.profile.copy()
        p.maxPayload = Protocol.effectivePayload(p)
        if (p.maxPayload < MIN_PAYLOAD)
            throw ConfigError("${hw.name} allows only ${p.maxPayload} B per message with the current radio settings")
        Protocol.applyProfile(p)
        return hw
    }
}
