package app.orchard.tv.lite

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URL
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.util.Locale
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory

class Film(val url: String, val caption: String?)

/** Apple's aerial films (assets/aerials.json, shared with the full app): the 1080p H.264 file of each. */
object Aerials {
    fun load(context: Context): List<Film> = runCatching {
        val json = context.assets.open("aerials.json").use { it.readBytes().toString(Charsets.UTF_8) }
        val videos = JSONObject(json).getJSONArray("videos")
        val zh = Locale.getDefault().language == "zh"
        List(videos.length()) { i ->
            val v = videos.getJSONObject(i)
            val caption = v.optJSONArray("captions")?.optJSONArray(0)?.let { c -> c.optString(if (zh) 1 else 2).takeIf { it.isNotEmpty() } }
            Film(v.getString("url"), caption?.replace(' ', ' '))
        }
    }.getOrDefault(emptyList())
}

/**
 * A local HTTP server that fetches Apple's films for the system player. Android 4.x trusts no
 * certificate that chains to Apple Root CA, its player cannot be given one, and sylvan.apple.com
 * now answers plain HTTP with a redirect to HTTPS. So MediaPlayer plays http://127.0.0.1/…, and
 * this fetches over HTTPS trusting the bundled Apple Root CA only, with TLS 1.2 switched on where
 * the system has it but leaves it off (Android 4.1-4.4). Range requests pass through, so the
 * player can seek and read the index at the end of the file.
 */
class FilmProxy(private val context: Context) {
    private var server: ServerSocket? = null

    /** The address MediaPlayer opens for [url]. */
    fun local(url: String): String {
        val s = server ?: start()
        return "http://127.0.0.1:${s.localPort}/f?u=" + URLEncoder.encode(url, "UTF-8")
    }

    fun stop() {
        runCatching { server?.close() }
        server = null
    }

    private fun start(): ServerSocket {
        val s = ServerSocket()
        s.bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0))
        server = s
        Log.i("AerialLite", "film proxy on ${s.localPort}")
        Thread({
            while (!s.isClosed) {
                val client = runCatching { s.accept() }.getOrNull() ?: break
                Thread({ serve(client) }, "FilmProxy-conn").start()
            }
        }, "FilmProxy").start()
        return s
    }

    private val factory: SSLSocketFactory by lazy {
        val cert = context.resources.openRawResource(R.raw.apple_root_ca).use {
            CertificateFactory.getInstance("X.509").generateCertificate(it)
        }
        val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null)
            setCertificateEntry("apple", cert)
        }
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(store) }
        val ssl = SSLContext.getInstance("TLS").apply { init(null, tmf.trustManagers, null) }
        AllProtocols(ssl.socketFactory)
    }

    // Not Socket.use: Socket is Closeable only from Android 4.4, and the interface call fails before it.
    private fun serve(sock: Socket) {
        try {
            runCatching {
                sock.soTimeout = 30_000
                val reader = BufferedReader(InputStreamReader(sock.getInputStream(), "ISO-8859-1"))
                val request = reader.readLine() ?: return
                var range: String? = null
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    if (line.startsWith("range:", ignoreCase = true)) range = line.substring(6).trim()
                }
                val path = request.split(' ').getOrNull(1) ?: return
                val url = URLDecoder.decode(path.substringAfter("u=", ""), "UTF-8")
                if (!url.startsWith("https://sylvan.apple.com/")) {
                    sock.getOutputStream().write("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n".toByteArray())
                    return
                }
                val conn = URL(url).openConnection() as HttpsURLConnection
                conn.sslSocketFactory = factory
                conn.connectTimeout = 8_000
                conn.readTimeout = 15_000
                if (range != null) conn.setRequestProperty("Range", range)
                val code = conn.responseCode
                Log.i("AerialLite", "film proxy: ${range ?: "whole"} -> $code ${conn.getHeaderField("Content-Length")}")
                val head = StringBuilder("HTTP/1.1 $code ${conn.responseMessage ?: ""}\r\n")
                for (h in listOf("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")) {
                    conn.getHeaderField(h)?.let { head.append(h).append(": ").append(it).append("\r\n") }
                }
                head.append("Connection: close\r\n\r\n")
                val out = sock.getOutputStream()
                out.write(head.toString().toByteArray(Charsets.ISO_8859_1))
                val body = if (code >= 400) conn.errorStream else conn.inputStream
                body?.use { input ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                    }
                }
                out.flush()
                conn.disconnect()
            }.onFailure { Log.i("AerialLite", "film proxy: ${it.javaClass.simpleName}: ${it.message}") }
        } finally {
            runCatching { sock.close() }
        }
    }
}
