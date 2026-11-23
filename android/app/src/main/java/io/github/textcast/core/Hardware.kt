package io.github.textcast.core

import java.io.File
import java.util.Locale

/**
 * Hardware profiles: how to talk to a particular LoRa module (port of
 * hardware.py). Custom profiles are the same JSON files as on the desktop;
 * Python-style regex groups (?P<name>...) are accepted.
 *
 * Template fields: {freq_mhz} {freq_hz} {sf} {bw_khz} {bw_hz} {bw_code} {cr}
 * {cr_index} {cr_denom} {preamble} {power} {sync16} {sync8} {public}
 * {public01} {hex} {len}
 */
class HardwareError(msg: String) : IllegalArgumentException(msg)

data class HardwareProfile(
    val name: String,
    val description: String = "",
    val driver: String = "at",
    val baud: Int = 9600,
    val lineEnding: String = "\r\n",
    val cmdDelay: Double = 0.3,
    val init: List<String> = emptyList(),
    val rxStart: List<String> = emptyList(),
    val send: String = "",
    val preSend: List<String> = emptyList(),
    val postSend: List<String> = emptyList(),
    val txDone: String = "",
    val txError: String = "",
    val rx: List<String> = emptyList(),
    val hexUpper: Boolean = true,
    val maxPayload: Map<String, Int> = emptyMap(),
    val firmwareOverhead: Int = 0,
    val syncWord: Boolean = true,
    val verified: Boolean = false,
    val notes: String = "",
) {
    fun validate(): List<String> {
        val problems = ArrayList<String>()
        if (driver != "at" && driver != "la66") problems.add("driver must be 'at' or 'la66'")
        if (driver == "at") {
            if ("{hex}" !in send) problems.add("'send' must contain {hex}")
            if (txDone.isEmpty()) problems.add("'tx_done' regex is required")
            if (rx.none { r -> try { "payload" in Hardware.regex(r).names } catch (e: Exception) { false } })
                problems.add("one 'rx' regex must have a (?P<payload>...) group")
            for (r in rx + listOf(txDone) + listOfNotNull(txError.ifEmpty { null })) {
                try { Hardware.regex(r) } catch (e: Exception) { problems.add("bad regex '$r': ${e.message}") }
            }
            try {
                Hardware.renderAll(init + rxStart + preSend + postSend, AirProfile())
                Hardware.render(send, AirProfile(), ByteArray(1))
            } catch (e: HardwareError) { problems.add(e.message ?: "bad template") }
        }
        return problems
    }

    fun toMap(): MutableMap<String, Any?> = linkedMapOf(
        "name" to name, "description" to description, "driver" to driver, "baud" to baud.toLong(),
        "line_ending" to lineEnding, "cmd_delay" to cmdDelay, "init" to init, "rx_start" to rxStart,
        "send" to send, "pre_send" to preSend, "post_send" to postSend, "tx_done" to txDone,
        "tx_error" to txError, "rx" to rx, "hex_upper" to hexUpper,
        "max_payload" to maxPayload.mapValues { it.value.toLong() }, "firmware_overhead" to firmwareOverhead.toLong(),
        "sync_word" to syncWord, "verified" to verified, "notes" to notes)
}

object Hardware {
    const val DEFAULT = "dragino-la66-p2p"

    private val LA66_LIMITS = mapOf("5" to 230, "6" to 230, "7" to 230, "8" to 230, "9" to 123,
        "10" to 59, "11" to 59, "12" to 59)

    val BUILTIN: LinkedHashMap<String, HardwareProfile> = linkedMapOf(
        DEFAULT to HardwareProfile(
            name = DEFAULT, description = "Dragino LA66 / LA66 USB with LA66 P2P firmware v1.2.4",
            driver = "la66", baud = 9600, firmwareOverhead = 1, maxPayload = LA66_LIMITS, verified = true,
            notes = "Tested with TextCast. Needs the P2P firmware, not the LoRaWAN firmware."),
        "rak3172-rui3" to HardwareProfile(
            name = "rak3172-rui3",
            description = "RAKwireless RAK3172 / RAK3272 / RAK3372 with RUI3 firmware (LoRa P2P)",
            driver = "at", baud = 115200, cmdDelay = 0.4,
            init = listOf("AT+NWM=0", "AT+P2P={freq_hz}:{sf}:{bw_khz}:{cr_index}:{preamble}:{power}",
                "AT+SYNCWORD={sync16}", "AT+IQINVER=0", "AT+ENCRY=0"),
            rxStart = listOf("AT+PRECV=0", "AT+PRECV=65534"),
            preSend = listOf("AT+PRECV=0"), send = "AT+PSEND={hex}", postSend = listOf("AT+PRECV=65534"),
            txDone = "\\+EVT:TXP2P DONE", txError = "AT_BUSY_ERROR|AT_PARAM_ERROR|AT_ERROR",
            rx = listOf("\\+EVT:RXP2P:(?P<rssi>-?\\d+):(?P<snr>-?\\d+):(?P<payload>[0-9A-Fa-f]+)"),
            maxPayload = mapOf("*" to 255), verified = false,
            notes = "From RAK's RUI3 documentation; not yet tested with TextCast. Some RUI3 versions expect " +
                "the bandwidth as a code (0=125) in AT+P2P, and the sync-word syntax may differ by version."),
        "seeed-wio-e5" to HardwareProfile(
            name = "seeed-wio-e5",
            description = "Seeed Wio-E5 / LoRa-E5 (STM32WLE5) with the factory AT firmware, TEST mode",
            driver = "at", baud = 9600, cmdDelay = 0.4,
            init = listOf("AT+MODE=TEST",
                "AT+TEST=RFCFG,{freq_mhz},SF{sf},{bw_khz},{preamble},{preamble},{power},ON,OFF,{public}"),
            rxStart = listOf("AT+TEST=RXLRPKT"), send = "AT+TEST=TXLRPKT,\"{hex}\"",
            postSend = listOf("AT+TEST=RXLRPKT"),
            txDone = "\\+TEST: TX DONE", txError = "\\+TEST: ERROR|ERROR\\(",
            rx = listOf("\\+TEST: LEN:\\s*\\d+,\\s*RSSI:\\s*(?P<rssi>-?\\d+),\\s*SNR:\\s*(?P<snr>-?\\d+)",
                "\\+TEST: RX \"(?P<payload>[0-9A-Fa-f]*)\""),
            maxPayload = mapOf("*" to 255), verified = false,
            notes = "From Seeed's LoRa-E5 AT specification; not yet tested with TextCast. " +
                "Bandwidth 125/250/500 kHz only."),
    )

    /** A regex with its named groups turned into numbered ones. */
    class Rx(val regex: Regex, val names: Map<String, Int>) {
        fun group(m: MatchResult, name: String): String? = names[name]?.let { m.groups[it]?.value }
    }

    /**
     * Compile a profile regex. Named groups, (?P<name>...) as in Python or
     * (?<name>...), become plain numbered groups: Android before 8.0 cannot
     * look groups up by name.
     */
    fun regex(r: String): Rx {
        val out = StringBuilder()
        val names = HashMap<String, Int>()
        var groups = 0
        var inClass = false
        var i = 0
        while (i < r.length) {
            val c = r[i]
            when {
                c == '\\' -> { out.append(c); if (i + 1 < r.length) out.append(r[i + 1]); i += 2; continue }
                inClass -> { if (c == ']') inClass = false; out.append(c) }
                c == '[' -> { inClass = true; out.append(c); if (r.startsWith("[]", i) || r.startsWith("[^]", i)) {
                    val skip = if (r[i + 1] == '^') 2 else 1; out.append(r, i + 1, i + 1 + skip); i += 1 + skip; continue } }
                c == '(' && r.startsWith("(?P<", i) || c == '(' && r.startsWith("(?<", i) &&
                    i + 3 < r.length && r[i + 3] != '=' && r[i + 3] != '!' -> {
                    val start = r.indexOf('<', i) + 1
                    val end = r.indexOf('>', start)
                    if (end < 0) throw IllegalArgumentException("unterminated group name")
                    groups++
                    names[r.substring(start, end)] = groups
                    out.append('(')
                    i = end + 1
                    continue
                }
                c == '(' && r.startsWith("(?", i) -> out.append(c)
                c == '(' -> { groups++; out.append(c) }
                else -> out.append(c)
            }
            i++
        }
        return Rx(Regex(out.toString()), names)
    }

    fun fields(p: AirProfile, payload: ByteArray? = null, hexUpper: Boolean = true): Map<String, String> {
        val d = linkedMapOf(
            "freq_mhz" to String.format(Locale.ROOT, "%.3f", p.freqMhz),
            "freq_hz" to Math.round(p.freqMhz * 1_000_000).toString(),
            "sf" to p.sf.toString(), "bw_khz" to fmtG(p.bwHz / 1000.0), "bw_hz" to p.bwHz.toString(),
            "bw_code" to (Regions.BW_CODES[p.bwHz] ?: 0).toString(), "cr" to p.cr.toString(),
            "cr_index" to (p.cr - 1).toString(), "cr_denom" to (p.cr + 4).toString(),
            "preamble" to p.preamble.toString(), "power" to p.txPowerDbm.toString(),
            "sync16" to if (p.privateSync) "1424" else "3444", "sync8" to if (p.privateSync) "12" else "34",
            "public" to if (p.privateSync) "OFF" else "ON", "public01" to if (p.privateSync) "0" else "1")
        if (payload != null) { d["hex"] = Bytes.hex(payload, hexUpper); d["len"] = payload.size.toString() }
        return d
    }

    private val FIELD = Regex("\\{([a-z0-9_]*)\\}")

    fun render(template: String, p: AirProfile, payload: ByteArray? = null, hexUpper: Boolean = true): String {
        val f = fields(p, payload, hexUpper)
        // {{ and }} are literal braces, as in Python's str.format
        val t = template.replace("{{", "\u0001").replace("}}", "\u0002")
        return FIELD.replace(t) { m -> f[m.groupValues[1]] ?: throw HardwareError("unknown template field '${m.groupValues[1]}'") }
            .replace("\u0001", "{").replace("\u0002", "}")
    }

    fun renderAll(templates: List<String>, p: AirProfile): List<String> = templates.map { render(it, p) }

    @Suppress("UNCHECKED_CAST")
    fun fromMap(d: Map<String, Any?>): HardwareProfile {
        val known = setOf("name", "description", "driver", "baud", "line_ending", "cmd_delay", "init", "rx_start",
            "send", "pre_send", "post_send", "tx_done", "tx_error", "rx", "hex_upper", "max_payload",
            "firmware_overhead", "sync_word", "verified", "notes")
        val unknown = d.keys - known
        if (unknown.isNotEmpty()) throw HardwareError("unknown profile keys: ${unknown.sorted().joinToString()}")
        val name = d["name"] as? String ?: throw HardwareError("profile needs a 'name'")
        fun s(k: String, def: String) = d[k] as? String ?: def
        fun l(k: String) = (d[k] as? List<Any?>)?.map { it.toString() } ?: emptyList()
        fun n(k: String, def: Double) = (d[k] as? Number)?.toDouble() ?: def
        fun b(k: String, def: Boolean) = d[k] as? Boolean ?: def
        return HardwareProfile(name, s("description", ""), s("driver", "at"), n("baud", 9600.0).toInt(),
            s("line_ending", "\r\n"), n("cmd_delay", 0.3), l("init"), l("rx_start"), s("send", ""),
            l("pre_send"), l("post_send"), s("tx_done", ""), s("tx_error", ""), l("rx"), b("hex_upper", true),
            (d["max_payload"] as? Map<String, Any?>)?.mapValues { (it.value as Number).toInt() } ?: emptyMap(),
            n("firmware_overhead", 0.0).toInt(), b("sync_word", true), b("verified", false), s("notes", ""))
    }

    fun loadFile(f: File): HardwareProfile {
        val hp = try { fromMap(Json.parseObject(f.readText())) } catch (e: HardwareError) { throw e }
            catch (e: Exception) { throw HardwareError("${f.name}: ${e.message}") }
        val problems = hp.validate()
        if (problems.isNotEmpty()) throw HardwareError("${f.name}: " + problems.joinToString("; "))
        return hp
    }

    /** Built-in names first, then custom JSON files in [folder] (id = file path). */
    fun available(folder: File?): List<Pair<String, String>> {
        val out = BUILTIN.map { (k, v) -> k to (v.description + if (v.verified) "" else "  (untested)") }.toMutableList()
        folder?.listFiles { f -> f.name.endsWith(".json") }?.sortedBy { it.name }?.forEach { f ->
            out.add(f.path to try { loadFile(f).description + "  (custom)" } catch (e: HardwareError) { "invalid: ${e.message}" })
        }
        return out
    }

    fun load(nameOrPath: String?): HardwareProfile {
        val n = nameOrPath?.ifEmpty { null } ?: DEFAULT
        BUILTIN[n]?.let { return it }
        val f = File(n)
        if (!f.exists()) throw HardwareError("unknown hardware '$n'. Built-in: ${BUILTIN.keys.joinToString()}")
        return loadFile(f)
    }
}
