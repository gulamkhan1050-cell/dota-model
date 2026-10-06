import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;

import java.io.File;
import java.io.FileInputStream;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.cert.X509Certificate;
import java.util.Collections;

/** Signs an APK with APK Signature Scheme v2 (Android 7+) and verifies it with a key from a JKS keystore: java Sign in.apk out.apk keystore.jks password alias */
public class Sign {
    public static void main(String[] a) throws Exception {
        KeyStore ks = KeyStore.getInstance("JKS");
        try (FileInputStream in = new FileInputStream(a[2])) {
            ks.load(in, a[3].toCharArray());
        }
        PrivateKey key = (PrivateKey) ks.getKey(a[4], a[3].toCharArray());
        X509Certificate cert = (X509Certificate) ks.getCertificate(a[4]);
        ApkSigner.SignerConfig cfg = new ApkSigner.SignerConfig.Builder("DOTA", key, Collections.singletonList(cert)).build();
        new ApkSigner.Builder(Collections.singletonList(cfg))
                .setInputApk(new File(a[0])).setOutputApk(new File(a[1]))
                .setV1SigningEnabled(false).setV2SigningEnabled(true)
                .build().sign();
        ApkVerifier.Result r = new ApkVerifier.Builder(new File(a[1])).build().verify();
        if (!r.isVerified() || !r.isVerifiedUsingV2Scheme()) {
            System.err.println("Signature check FAILED: " + r.getErrors());
            System.exit(1);
        }
        System.out.println("Signed and verified (v2): " + a[1]);
    }
}
