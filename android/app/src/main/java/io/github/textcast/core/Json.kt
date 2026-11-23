package io.github.textcast.core

/**
 * Small JSON reader/writer, so the core runs on the plain JVM (tests) and on
 * Android without org.json. Objects become MutableMap<String, Any?> (insertion
 * order kept), arrays MutableList<Any?>, integers Long, other numbers Double.
 * Files written here are read by the Python apps and vice versa.
 */
object Json {
    class ParseError(msg: String) : IllegalArgumentException(msg)

    fun parse(s: String): Any? {
        val p = Parser(s)
        p.ws()
        val v = p.value()
        p.ws()
        if (p.i != s.length) throw ParseError("trailing data at ${p.i}")
        return v
    }

    @Suppress("UNCHECKED_CAST")
    fun parseObject(s: String): MutableMap<String, Any?> =
        parse(s) as? MutableMap<String, Any?> ?: throw ParseError("not a JSON object")

    fun stringify(v: Any?, indent: Int = 0): String =
        StringBuilder().also { write(it, v, indent, 0) }.toString()

    private fun write(sb: StringBuilder, v: Any?, indent: Int, level: Int) {
        fun nl(l: Int) { if (indent > 0) { sb.append('\n'); repeat(l * indent) { sb.append(' ') } } }
        when (v) {
            null -> sb.append("null")
            is Boolean -> sb.append(if (v) "true" else "false")
            is Int, is Long, is Short, is Byte -> sb.append(v.toString())
            is Float, is Double -> {
                val d = (v as Number).toDouble()
                if (d.isNaN() || d.isInfinite()) sb.append("null")
                else if (d == Math.floor(d) && Math.abs(d) < 1e15) sb.append(d.toLong()).append(".0")
                else sb.append(d.toString())
            }
            is String -> quote(sb, v)
            is Map<*, *> -> {
                if (v.isEmpty()) { sb.append("{}"); return }
                sb.append('{')
                var first = true
                for ((k, x) in v) {
                    if (!first) sb.append(',')
                    first = false
                    nl(level + 1)
                    quote(sb, k.toString())
                    sb.append(if (indent > 0) ": " else ":")
                    write(sb, x, indent, level + 1)
                }
                nl(level); sb.append('}')
            }
            is Iterable<*> -> {
                val items = v.toList()
                if (items.isEmpty()) { sb.append("[]"); return }
                sb.append('[')
                items.forEachIndexed { idx, x ->
                    if (idx > 0) sb.append(',')
                    nl(level + 1); write(sb, x, indent, level + 1)
                }
                nl(level); sb.append(']')
            }
            else -> quote(sb, v.toString())
        }
    }

    private fun quote(sb: StringBuilder, s: String) {
        sb.append('"')
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\""); '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n"); '\r' -> sb.append("\\r"); '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b"); '\u000C' -> sb.append("\\f")
                else -> if (c < ' ') sb.append(String.format("\\u%04x", c.code)) else sb.append(c)
            }
        }
        sb.append('"')
    }

    private class Parser(val s: String) {
        var i = 0

        fun ws() { while (i < s.length && s[i] in " \t\r\n") i++ }

        fun fail(msg: String): Nothing = throw ParseError("$msg at $i")

        fun value(): Any? {
            if (i >= s.length) fail("unexpected end")
            return when (s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> str()
                't' -> lit("true", true)
                'f' -> lit("false", false)
                'n' -> lit("null", null)
                else -> num()
            }
        }

        fun lit(word: String, v: Any?): Any? {
            if (!s.startsWith(word, i)) fail("bad literal")
            i += word.length
            return v
        }

        fun obj(): MutableMap<String, Any?> {
            val m = LinkedHashMap<String, Any?>()
            i++; ws()
            if (i < s.length && s[i] == '}') { i++; return m }
            while (true) {
                ws()
                if (i >= s.length || s[i] != '"') fail("expected key")
                val k = str()
                ws()
                if (i >= s.length || s[i] != ':') fail("expected ':'")
                i++; ws()
                m[k] = value()
                ws()
                if (i >= s.length) fail("unexpected end")
                when (s[i]) {
                    ',' -> i++
                    '}' -> { i++; return m }
                    else -> fail("expected ',' or '}'")
                }
            }
        }

        fun arr(): MutableList<Any?> {
            val l = ArrayList<Any?>()
            i++; ws()
            if (i < s.length && s[i] == ']') { i++; return l }
            while (true) {
                ws(); l.add(value()); ws()
                if (i >= s.length) fail("unexpected end")
                when (s[i]) {
                    ',' -> i++
                    ']' -> { i++; return l }
                    else -> fail("expected ',' or ']'")
                }
            }
        }

        fun str(): String {
            val sb = StringBuilder()
            i++
            while (true) {
                if (i >= s.length) fail("unterminated string")
                val c = s[i++]
                when (c) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        if (i >= s.length) fail("bad escape")
                        when (val e = s[i++]) {
                            '"' -> sb.append('"'); '\\' -> sb.append('\\'); '/' -> sb.append('/')
                            'b' -> sb.append('\b'); 'f' -> sb.append('\u000C')
                            'n' -> sb.append('\n'); 'r' -> sb.append('\r'); 't' -> sb.append('\t')
                            'u' -> {
                                if (i + 4 > s.length) fail("bad \\u escape")
                                sb.append(s.substring(i, i + 4).toInt(16).toChar()); i += 4
                            }
                            else -> fail("bad escape '\\$e'")
                        }
                    }
                    else -> sb.append(c)
                }
            }
        }

        fun num(): Any {
            val start = i
            if (i < s.length && s[i] == '-') i++
            while (i < s.length && (s[i].isDigit() || s[i] in ".eE+-")) i++
            val t = s.substring(start, i)
            if (t.isEmpty() || t == "-") fail("bad value")
            return if (t.any { it in ".eE" }) t.toDouble() else t.toLongOrNull() ?: t.toDouble()
        }
    }
}

// -- typed access helpers ---------------------------------------------------------

@Suppress("UNCHECKED_CAST")
fun Any?.asMap(): MutableMap<String, Any?> = this as? MutableMap<String, Any?> ?: LinkedHashMap()

fun Map<String, Any?>.str(key: String): String? = this[key] as? String
fun Map<String, Any?>.long(key: String, default: Long = 0): Long = (this[key] as? Number)?.toLong() ?: default
fun Map<String, Any?>.bool(key: String): Boolean = this[key] as? Boolean ?: false

@Suppress("UNCHECKED_CAST")
fun MutableMap<String, Any?>.obj(key: String): MutableMap<String, Any?> =
    this[key] as? MutableMap<String, Any?> ?: LinkedHashMap<String, Any?>().also { this[key] = it }
