import java.io.ByteArrayInputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;
import javax.net.ssl.SSLException;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509ExtendedTrustManager;

/**
 * What a build behind a company's proxy needs of Java, in one file so that
 * `java scripts/BuildNetwork.java ...` runs it with nothing compiled first.
 *
 *   trust BUNDLE STORE   writes STORE, a trust store: the certificates Java
 *                        ships with and, beside them, those of BUNDLE (PEM)
 *   report NAME=URL...   says what each URL answers, followed through its
 *                        redirects as a download is; when Java will not take
 *                        a certificate, who signed it
 *   first FILE REPOSITORY...
 *                        prints the first REPOSITORY that has FILE, and
 *                        nothing when none of them does
 *
 * Nothing here trusts what it was not told to: `report` checks as any Java
 * program does, with the store the build was given.
 */
public final class BuildNetwork {
    private static final char[] PASSWORD = "changeit".toCharArray();
    private static final Pattern PEM = Pattern.compile("-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", Pattern.DOTALL);

    public static void main(String[] args) throws Exception {
        if (args.length == 3 && args[0].equals("trust")) {
            trust(Path.of(args[1]), Path.of(args[2]));
        } else if (args.length > 1 && args[0].equals("report")) {
            for (int i = 1; i < args.length; i++) {
                String[] named = args[i].split("=", 2);
                System.out.printf("  %-15s %s%n", named[0], answer(named[1]));
            }
        } else if (args.length > 2 && args[0].equals("first")) {
            for (int i = 2; i < args.length; i++) {
                if (answer(args[i] + "/" + args[1]).startsWith("HTTP 2")) {
                    System.out.println(args[i]);
                    return;
                }
            }
        } else {
            System.err.println("usage: BuildNetwork trust BUNDLE STORE | report NAME=URL... | first FILE REPOSITORY...");
            System.exit(2);
        }
    }

    /** The trust manager a Java program starts with: Java's own certificates, or the store it was pointed at. */
    private static X509ExtendedTrustManager usual() throws Exception {
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init((KeyStore) null);
        return (X509ExtendedTrustManager) factory.getTrustManagers()[0];
    }

    private static void trust(Path bundle, Path target) throws Exception {
        KeyStore store = KeyStore.getInstance("PKCS12");
        store.load(null, null);
        int shipped = 0;
        for (X509Certificate known : usual().getAcceptedIssuers()) {
            store.setCertificateEntry("java-" + shipped++, known);
        }
        CertificateFactory x509 = CertificateFactory.getInstance("X.509");
        // A bundle may carry notes between its certificates: only the certificates are read.
        Matcher block = PEM.matcher(Files.readString(bundle, StandardCharsets.ISO_8859_1));
        int added = 0;
        while (block.find()) {
            try {
                byte[] one = block.group().getBytes(StandardCharsets.ISO_8859_1);
                store.setCertificateEntry("bundle-" + added, x509.generateCertificate(new ByteArrayInputStream(one)));
                added++;
            } catch (CertificateException unreadable) {
                // One this Java cannot read does not cost the rest.
            }
        }
        try (OutputStream out = Files.newOutputStream(target)) {
            store.store(out, PASSWORD);
        }
        System.out.println("Trusting what " + bundle + " holds beside Java's own certificates: " + added + " and " + shipped);
    }

    private static String answer(String url) {
        URI at = URI.create(url);
        Shown shown = null;
        try {
            shown = new Shown(usual());
            SSLContext tls = SSLContext.getInstance("TLS");
            tls.init(null, new TrustManager[] {shown}, null);
            for (int hop = 0; hop < 8; hop++) {
                HttpURLConnection connection = (HttpURLConnection) at.toURL().openConnection();
                if (connection instanceof HttpsURLConnection secure) {
                    secure.setSSLSocketFactory(tls.getSocketFactory());
                }
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(15_000);
                connection.setReadTimeout(15_000);
                // The first byte is enough to know it is there.
                connection.setRequestProperty("Range", "bytes=0-0");
                int status = connection.getResponseCode();
                String next = connection.getHeaderField("Location");
                connection.disconnect();
                if (status / 100 != 3 || next == null) {
                    return "HTTP " + status + " from " + at.getHost();
                }
                at = at.resolve(next);
            }
            return "redirects without end, last to " + at.getHost();
        } catch (SSLException refused) {
            // The usual case has a long name and a short meaning.
            String why = String.valueOf(refused.getMessage()).contains("PKIX") ? "its certificate is not one Java trusts" : brief(refused);
            return "no TLS with " + at.getHost() + ": " + why + (shown == null ? "" : shown.signer());
        } catch (Exception failure) {
            return "no answer from " + at.getHost() + ": " + brief(failure);
        }
    }

    private static String brief(Exception failure) {
        String said = failure.getClass().getSimpleName() + (failure.getMessage() == null ? "" : " " + failure.getMessage());
        return said.length() > 110 ? said.substring(0, 110) + "..." : said;
    }

    /** Checks exactly as Java does, and remembers what it was shown, to say who signed what was refused. */
    private static final class Shown extends X509ExtendedTrustManager {
        private final X509ExtendedTrustManager usual;
        private X509Certificate[] last;

        Shown(X509ExtendedTrustManager usual) {
            this.usual = usual;
        }

        String signer() {
            if (last == null || last.length == 0) return "";
            String issuer = last[last.length - 1].getIssuerX500Principal().getName();
            return "; it is signed by " + issuer.replaceFirst("^.*?CN=([^,]+).*$", "$1");
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String type) throws CertificateException {
            usual.checkClientTrusted(chain, type);
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String type) throws CertificateException {
            last = chain;
            usual.checkServerTrusted(chain, type);
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String type, Socket socket) throws CertificateException {
            usual.checkClientTrusted(chain, type, socket);
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String type, Socket socket) throws CertificateException {
            last = chain;
            usual.checkServerTrusted(chain, type, socket);
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String type, SSLEngine engine) throws CertificateException {
            usual.checkClientTrusted(chain, type, engine);
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String type, SSLEngine engine) throws CertificateException {
            last = chain;
            usual.checkServerTrusted(chain, type, engine);
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return usual.getAcceptedIssuers();
        }
    }
}
