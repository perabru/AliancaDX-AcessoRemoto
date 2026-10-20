// Uses the official apksig classes shipped inside Android Build Tools.
// Invoked in JDK source-file mode; receives only a public APK, never keys.
import com.android.apksig.ApkVerifier;
import java.io.File;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.util.List;

public final class VerifySignerBlocks {
    private static String digest(X509Certificate certificate) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded());
        StringBuilder text = new StringBuilder();
        for (byte value : bytes) text.append(String.format("%02x", value & 255));
        return text.toString();
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Supply one public APK.");
        ApkVerifier.Result result = new ApkVerifier.Builder(new File(args[0]))
                .setMinCheckedPlatformVersion(26).setMaxCheckedPlatformVersion(37).build().verify();
        if (!result.isVerified() || result.containsErrors() || !result.getAllErrors().isEmpty())
            throw new IllegalStateException("APK signature verification failed.");
        int warnings = result.getWarnings().size();
        for (var item : result.getV2SchemeSigners()) warnings += item.getWarnings().size();
        for (var item : result.getV3SchemeSigners()) warnings += item.getWarnings().size();
        for (var item : result.getV31SchemeSigners()) warnings += item.getWarnings().size();
        if (warnings != 0) throw new IllegalStateException("Verification has warnings.");
        if (result.getV2SchemeSigners().size() != 1 || result.getV3SchemeSigners().size() != 1
                || result.getV31SchemeSigners().size() != 1)
            throw new IllegalStateException("Unexpected signing block count.");
        var v2 = result.getV2SchemeSigners().get(0);
        var v3 = result.getV3SchemeSigners().get(0);
        var v31 = result.getV31SchemeSigners().get(0);
        List<X509Certificate> lineage = result.getSigningCertificateLineage().getCertificatesInLineage();
        if (lineage.size() != 2) throw new IllegalStateException("Unexpected signing lineage size.");
        System.out.println("{\"verified\":true,\"warnings\":0,\"min_sdk\":26,\"max_sdk\":37,"
                + "\"v1\":" + result.isVerifiedUsingV1Scheme()
                + ",\"v2\":" + result.isVerifiedUsingV2Scheme()
                + ",\"v3\":" + result.isVerifiedUsingV3Scheme()
                + ",\"v31\":" + result.isVerifiedUsingV31Scheme()
                + ",\"v4\":" + result.isVerifiedUsingV4Scheme()
                + ",\"v2_certificate_sha256\":\"" + digest(v2.getCertificate()) + "\""
                + ",\"v3_certificate_sha256\":\"" + digest(v3.getCertificate()) + "\""
                + ",\"v31_certificate_sha256\":\"" + digest(v31.getCertificate()) + "\""
                + ",\"v3_min_sdk\":" + v3.getMinSdkVersion()
                + ",\"v3_max_sdk\":" + v3.getMaxSdkVersion()
                + ",\"v31_min_sdk\":" + v31.getMinSdkVersion()
                + ",\"v31_max_sdk\":" + v31.getMaxSdkVersion()
                + ",\"lineage\":[\"" + digest(lineage.get(0)) + "\",\"" + digest(lineage.get(1)) + "\"]}");
    }
}
