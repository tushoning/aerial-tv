package app.orchard.tv.lite

import android.os.Build
import java.io.File
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.Socket
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/**
 * HTTP for Lite's downloads (icon library, weather). Android 4.1-4.4 support TLS 1.2 but leave it
 * off, and most sites now refuse anything older: [open] turns on every TLS version the system has.
 */
object Net {
    private val tls: SSLSocketFactory? by lazy {
        if (Build.VERSION.SDK_INT >= 20) null
        else runCatching { AllProtocols(SSLContext.getInstance("TLS").apply { init(null, null, null) }.socketFactory) }.getOrNull()
    }

    fun open(url: String): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = 8_000
        readTimeout = 15_000
        if (this is HttpsURLConnection) tls?.let { sslSocketFactory = it }
    }

    fun text(url: String): String {
        val c = open(url)
        try {
            if (c.responseCode !in 200..299) error("HTTP ${c.responseCode}")
            return c.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } finally {
            c.disconnect()
        }
    }

    /** Into a temporary file first, so a broken download never stands as the file. */
    fun download(url: String, to: File) {
        val tmp = File(to.path + ".part")
        val c = open(url)
        try {
            if (c.responseCode !in 200..299) error("HTTP ${c.responseCode}")
            c.inputStream.use { input -> java.io.FileOutputStream(tmp).use { input.copyTo(it) } }
        } finally {
            c.disconnect()
        }
        if (!tmp.renameTo(to)) { tmp.delete(); error("could not keep $to") }
    }
}

/** Turns on every protocol the system supports, TLS 1.2 included where it is off by default. */
class AllProtocols(private val base: SSLSocketFactory) : SSLSocketFactory() {
    private fun on(s: Socket): Socket {
        if (s is SSLSocket) s.enabledProtocols = s.supportedProtocols.filter { it.startsWith("TLS") }.toTypedArray()
        return s
    }
    override fun getDefaultCipherSuites(): Array<String> = base.defaultCipherSuites
    override fun getSupportedCipherSuites(): Array<String> = base.supportedCipherSuites
    override fun createSocket(s: Socket, host: String, port: Int, autoClose: Boolean) = on(base.createSocket(s, host, port, autoClose))
    override fun createSocket(host: String, port: Int) = on(base.createSocket(host, port))
    override fun createSocket(host: String, port: Int, localHost: InetAddress, localPort: Int) = on(base.createSocket(host, port, localHost, localPort))
    override fun createSocket(host: InetAddress, port: Int) = on(base.createSocket(host, port))
    override fun createSocket(address: InetAddress, port: Int, localAddress: InetAddress, localPort: Int) = on(base.createSocket(address, port, localAddress, localPort))
}
