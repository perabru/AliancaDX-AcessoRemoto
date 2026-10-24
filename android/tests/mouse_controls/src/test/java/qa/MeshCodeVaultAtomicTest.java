package qa;

import android.content.Context;
import br.com.perabru.minianydesk.MeshCodeVault;
import br.com.perabru.minianydesk.MeshPairing;
import java.io.File;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Arrays;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import static org.junit.Assert.*;

/** Actual Android AtomicFile and app-private files with a synthetic JCE key provider.
 * Does not simulate AndroidKeyStore hardware or claim a physical-device test.
 */
@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35)
public class MeshCodeVaultAtomicTest {
    private static class Keys implements MeshCodeVault.KeyProvider {
        final SecretKey key = new SecretKeySpec(new byte[32], "AES");
        int creates = 0;
        @Override public SecretKey existing() { return key; }
        @Override public SecretKey create() { creates++; return key; }
    }

    private static MeshPairing pairing() throws Exception {
        byte[] body = ByteBuffer.allocate(67).put((byte)1).put(new byte[32])
            .putShort((short)64321).put(new byte[32]).array();
        Arrays.fill(body,35,67,(byte)0xAA);
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        digest.update("MDA6-check/".getBytes(StandardCharsets.UTF_8));
        byte[] payload = Arrays.copyOf(body,71);
        System.arraycopy(digest.digest(body),0,payload,67,4);
        String alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
        StringBuilder code = new StringBuilder("MDA6-");
        int bits=0,buffer=0;
        for(byte value:payload) {
            buffer=(buffer<<8)|(value&255);bits+=8;
            while(bits>=5){bits-=5;code.append(alphabet.charAt((buffer>>>bits)&31));}
        }
        if(bits>0)code.append(alphabet.charAt((buffer<<(5-bits))&31));
        return MeshPairing.Companion.parseCode(code.toString());
    }

    private File base(Context context,MeshPairing pairing) {
        String publicId=pairing.getAddress().substring(5,pairing.getAddress().length()-8);
        return new File(new File(context.getFilesDir(),"remembered_codes"),publicId+".bin");
    }

    @Test public void atomicBackupWithoutBaseRecoversForAFreshVaultInstance() throws Exception {
        Context context=RuntimeEnvironment.getApplication();Keys keys=new Keys();MeshPairing p=pairing();
        new MeshCodeVault(context,keys).save(p);File file=base(context,p);
        byte[] encrypted=Files.readAllBytes(file.toPath());
        File backup=new File(file.getPath()+".bak");
        Files.move(file.toPath(),backup.toPath(),StandardCopyOption.REPLACE_EXISTING);
        assertFalse(file.exists());
        assertEquals(p.getCode(),new MeshCodeVault(context,keys).load(p.getAddress()).getCode());
        assertArrayEquals(encrypted,Files.readAllBytes(file.toPath()));
        assertFalse(backup.exists());assertEquals(0,keys.creates);
    }

    @Test public void committedBaseWinsOverAnInterruptedNewWrite() throws Exception {
        Context context=RuntimeEnvironment.getApplication();Keys keys=new Keys();MeshPairing p=pairing();
        MeshCodeVault vault=new MeshCodeVault(context,keys);vault.save(p);File file=base(context,p);
        byte[] encrypted=Files.readAllBytes(file.toPath());File pending=new File(file.getPath()+".new");
        Files.write(pending.toPath(),new byte[]{3,4,5});
        assertEquals(p.getCode(),vault.load(p.getAddress()).getCode());
        assertArrayEquals(encrypted,Files.readAllBytes(file.toPath()));assertFalse(pending.exists());
    }

    @Test public void oversizedCorruptCiphertextIsPreservedAndReported() throws Exception {
        Context context=RuntimeEnvironment.getApplication();Keys keys=new Keys();MeshPairing p=pairing();
        MeshCodeVault vault=new MeshCodeVault(context,keys);vault.save(p);File file=base(context,p);
        byte[] invalid=new byte[513];Arrays.fill(invalid,(byte)7);Files.write(file.toPath(),invalid);
        try { vault.load(p.getAddress());fail("corruption must not be absence"); }
        catch(IOException expected) { assertTrue(expected.getMessage().contains("ler")); }
        assertArrayEquals(invalid,Files.readAllBytes(file.toPath()));assertEquals(0,keys.creates);
    }

    @Test public void forgettingRemovesBaseBackupAndUncommittedWrite() throws Exception {
        Context context=RuntimeEnvironment.getApplication();Keys keys=new Keys();MeshPairing p=pairing();
        MeshCodeVault vault=new MeshCodeVault(context,keys);vault.save(p);File file=base(context,p);
        File backup=new File(file.getPath()+".bak"),pending=new File(file.getPath()+".new");
        Files.copy(file.toPath(),backup.toPath(),StandardCopyOption.REPLACE_EXISTING);
        Files.write(pending.toPath(),new byte[]{3,4,5});
        vault.forget(p.getAddress());
        assertNull(vault.load(p.getAddress()));assertFalse(file.exists());assertFalse(backup.exists());assertFalse(pending.exists());
    }
}
