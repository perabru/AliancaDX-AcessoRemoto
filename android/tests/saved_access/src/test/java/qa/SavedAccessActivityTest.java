package qa;

import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.CheckBox;
import br.com.perabru.minianydesk.*;
import kotlin.LazyKt;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowAlertDialog;
import org.robolectric.util.ReflectionHelpers;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.net.Socket;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import static org.junit.Assert.*;

/** Production Activity/session/book/vault logic, with real SharedPreferences and synthetic JCE keys.
 * Build-only R=0 disables the logo asset; no phone, Host, mesh or TLS connection is simulated as real. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35, qualifiers = "mdpi")
public class SavedAccessActivityTest {
    private final List<ActivityController<MainActivity>> windows = new ArrayList<>();
    private final Map<String, byte[]> encrypted = new HashMap<>();
    private MeshCodeVault vault;
    private Context context;

    @Before public void clearFixture() {
        context = RuntimeEnvironment.getApplication();
        assertTrue(context.getSharedPreferences("saved_computers", 0).edit().clear().commit());
        assertTrue(context.getSharedPreferences("native_connection", 0).edit().clear().commit());
        vault = new MeshCodeVault(new MeshCodeVault.Store() {
            public byte[] read(String address) { byte[] bytes=encrypted.get(address); return bytes==null?null:bytes.clone(); }
            public void write(String address, byte[] bytes) { encrypted.put(address,bytes.clone()); }
            public void delete(String address) { encrypted.remove(address); }
        }, new MeshCodeVault.KeyProvider() {
            public SecretKey existing() { return new SecretKeySpec(new byte[32],"AES"); }
            public SecretKey create() { return existing(); }
        });
    }

    @After public void closeWindows() {
        for(ActivityController<MainActivity> window:windows) window.pause().stop().destroy();
        windows.clear();
    }

    private ActivityController<MainActivity> start() {
        ActivityController<MainActivity> window=Robolectric.buildActivity(MainActivity.class).setup().visible();
        windows.add(window); inject(selected(window.get()));
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        return window;
    }
    private Object workspace(MainActivity activity) { return ReflectionHelpers.getField(activity,"workspace"); }
    private Object selected(MainActivity activity) { return invoke(workspace(activity),"getSelected"); }
    private void inject(Object session) { ReflectionHelpers.setField(session,"codeVault$delegate",LazyKt.lazy(()->vault)); }
    private static Object invoke(Object target,String name,Object...args) {
        try {
            for(Method method:target.getClass().getDeclaredMethods())
                if((method.getName().equals(name)||method.getName().startsWith(name+"$"))&&method.getParameterCount()==args.length) {
                    method.setAccessible(true);return method.invoke(target,args);
                }
            throw new NoSuchMethodException(name);
        } catch(Exception e) { throw new AssertionError("Production method "+name,e); }
    }
    private static EditText input(Object owner,String name) { return ReflectionHelpers.getField(owner,name); }
    private static MeshPairing pairing(int seed) {
        try {
            byte[] body=new byte[67];body[0]=1;
            for(int i=0;i<32;i++){body[1+i]=(byte)(i+seed);body[35+i]=(byte)170;}
            ByteBuffer.wrap(body,33,2).putShort((short)64321);
            byte[] checkInput=new byte[11+67];byte[] prefix="MDA6-check/".getBytes(java.nio.charset.StandardCharsets.UTF_8);
            checkInput=new byte[prefix.length+body.length];System.arraycopy(prefix,0,checkInput,0,prefix.length);System.arraycopy(body,0,checkInput,prefix.length,body.length);
            byte[] checksum=MessageDigest.getInstance("SHA-256").digest(checkInput),payload=new byte[71];
            System.arraycopy(body,0,payload,0,67);System.arraycopy(checksum,0,payload,67,4);
            String alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";StringBuilder code=new StringBuilder("MDA6-");int bits=0,buffer=0;
            for(byte value:payload){buffer=(buffer<<8)|(value&255);bits+=8;while(bits>=5){bits-=5;code.append(alphabet.charAt((buffer>>>bits)&31));}}
            if(bits>0)code.append(alphabet.charAt((buffer<<(5-bits))&31));
            return MeshPairing.Companion.parseCode(code.toString());
        } catch(Exception error) { throw new AssertionError(error); }
    }
    private String invitation(MeshPairing pairing,String alias,String password) {
        return "AliançaDX Host 7.0.0\r\nComputador: "+alias+"\r\nCódigo privado: "+pairing.getCode()+"\r\nSenha: "+password;
    }
    private static EditText findEdit(View view) {
        if(view instanceof EditText)return (EditText)view;
        if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++){
            EditText found=findEdit(((ViewGroup)view).getChildAt(i));if(found!=null)return found;
        }
        return null;
    }

    @Test public void explicitSaveBeforeAuthenticationSurvivesActivityRestartAndRequiresCurrentPassword() throws Exception {
        ActivityController<MainActivity> first=start();Object session=selected(first.get());MeshPairing pairing=pairing(0);
        input(session,"privateCode").setText(invitation(pairing,"PC da sala","fixture-old-password"));
        assertNotNull(invoke(session,"saveCurrentAccess"));
        SavedComputer saved=new ComputerBook(context).entries().get(0);
        assertEquals("PC da sala",saved.getName());assertNull(saved.getLastSeen());
        assertEquals(pairing.getCode(),vault.load(pairing.getAddress()).getCode());
        assertEquals(pairing.getCode(),input(session,"privateCode").getText().toString());
        assertFalse(context.getSharedPreferences("native_connection",0).getAll().values().contains("fixture-old-password"));
        invoke(session,"stopAll","Conexão cancelada.");
        assertEquals("",input(session,"password").getText().toString());
        assertEquals(pairing.getCode(),vault.load(pairing.getAddress()).getCode());
        first.pause().stop().destroy();windows.remove(first);
        ActivityController<MainActivity> reloaded=start();Object restored=selected(reloaded.get());
        assertEquals("computers",ReflectionHelpers.getField(restored,"selectedPage"));
        ((android.widget.Switch)ReflectionHelpers.getField(restored,"localModeToggle")).setChecked(true);
        assertFalse(((View)ReflectionHelpers.getField(restored,"saveAccessButton")).isEnabled());
        invoke(restored,"prepareSavedComputer",saved);
        assertTrue("Switching back from local IP to a saved invitation must enable explicit saving",
            ((View)ReflectionHelpers.getField(restored,"saveAccessButton")).isEnabled());
        assertEquals(pairing.getCode(),input(restored,"privateCode").getText().toString());
        assertEquals("",input(restored,"password").getText().toString());
        assertTrue(((CheckBox)ReflectionHelpers.getField(restored,"rememberCode")).isChecked());
        invoke(restored,"connectController");
        assertEquals(false,ReflectionHelpers.getField(restored,"connecting"));
        assertNull(ReflectionHelpers.getField(restored,"peer"));
        assertNotNull("Password must be supplied before network/authentication",input(restored,"password").getError());
    }

    @Test public void rememberSavesBeforeMissingPasswordAndCancelDoesNotEraseSavedCode() throws Exception {
        MainActivity activity=start().get();Object session=selected(activity);MeshPairing p=pairing(1);
        input(session,"privateCode").setText(p.getCode());
        ((CheckBox)ReflectionHelpers.getField(session,"rememberCode")).setChecked(true);
        invoke(session,"connectController");
        assertFalse((Boolean)ReflectionHelpers.getField(session,"connecting"));
        assertEquals(1,new ComputerBook(context).entries().size());
        assertEquals(p.getCode(),vault.load(p.getAddress()).getCode());
        assertNotNull(input(session,"password").getError());
        invoke(session,"stopAll","Conexão cancelada.");
        assertEquals(p.getCode(),vault.load(p.getAddress()).getCode());
        assertEquals("",input(session,"password").getText().toString());
    }

    @Test public void cancellingUpdatedInvitationPreservesCardAndConfirmedUpdateKeepsItsIdentity() throws Exception {
        MainActivity activity=start().get();Object session=selected(activity);MeshPairing old=pairing(0),next=pairing(2);
        input(session,"privateCode").setText(old.getCode());invoke(session,"saveCurrentAccess");
        ComputerBook book=new ComputerBook(context);SavedComputer saved=book.entries().get(0);book.markSeen(saved.getId(),1000);
        invoke(session,"updateSavedCode",saved);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        AlertDialog dialog=ShadowAlertDialog.getLatestAlertDialog();
        findEdit(dialog.getWindow().getDecorView()).setText(invitation(next,"Outro nome","fixture-should-not-persist"));
        dialog.getButton(DialogInterface.BUTTON_NEGATIVE).performClick();
        assertEquals(old.getAddress(),book.entries().get(0).getAddress());assertNull(vault.load(next.getAddress()));
        invoke(session,"updateSavedCode",saved);Shadows.shadowOf(Looper.getMainLooper()).idle();dialog=ShadowAlertDialog.getLatestAlertDialog();
        findEdit(dialog.getWindow().getDecorView()).setText(invitation(next,"Outro nome","fixture-should-not-persist"));
        dialog.getButton(DialogInterface.BUTTON_POSITIVE).performClick();
        SavedComputer updated=book.entries().get(0);
        assertEquals(saved.getId(),updated.getId());assertEquals(saved.getName(),updated.getName());assertEquals(Long.valueOf(1000),updated.getLastSeen());
        assertEquals("Update dialog result: "+Shadows.shadowOf(ShadowAlertDialog.getLatestAlertDialog()).getMessage(),
            next.getAddress(),updated.getAddress());assertEquals(next.getCode(),vault.load(next.getAddress()).getCode());
        assertEquals("",input(session,"password").getText().toString());
        assertFalse(context.getSharedPreferences("native_connection",0).getAll().values().contains("fixture-should-not-persist"));
    }

    @Test public void savedCardsReuseTheirOwnerAndRespectFourIndependentSlots() {
        MainActivity activity=start().get();ComputerBook book=new ComputerBook(context);Object first=selected(activity);
        List<SavedComputer> records=new ArrayList<>();
        for(int i=0;i<5;i++){MeshPairing p=pairing(i);records.add(book.add("PC "+i,p.getAddress(),p.getPort()));}
        for(int i=0;i<4;i++){
            invoke(activity,"openSavedComputer",records.get(i));Object owner=selected(activity);inject(owner);
            ReflectionHelpers.setField(owner,"connecting",true);
            ReflectionHelpers.setField(owner,"connectingEndpoint",new kotlin.Pair<>(records.get(i).getAddress(),records.get(i).getPort()));
        }
        assertEquals(4,((Number)invoke(workspace(activity),"getSize")).intValue());
        assertTrue((Boolean)ReflectionHelpers.getField(first,"connecting"));
        invoke(activity,"openSavedComputer",records.get(0));assertSame(first,selected(activity));
        invoke(activity,"openSavedComputer",records.get(4));
        assertEquals(4,((Number)invoke(workspace(activity),"getSize")).intValue());
        assertSame(first,selected(activity));assertTrue((Boolean)ReflectionHelpers.getField(first,"connecting"));
    }

    @Test public void staleCardReloadsDurableEndpointBeforeRoutingAndDeletedOrCorruptCardsCannotFallback() {
        MainActivity activity=start().get();ComputerBook book=new ComputerBook(context);Object first=selected(activity);
        MeshPairing old=pairing(0),replacement=pairing(2);
        SavedComputer stale=book.add("Meu PC",old.getAddress(),old.getPort());
        invoke(activity,"openSavedComputer",stale);
        ReflectionHelpers.setField(first,"connecting",true);
        ReflectionHelpers.setField(first,"connectingEndpoint",new kotlin.Pair<>(old.getAddress(),old.getPort()));
        // Another instance updates the same card while this UI still has its old row object.
        new ComputerBook(context).update(stale.getId(),stale.getName(),replacement.getAddress(),replacement.getPort());
        invoke(activity,"openSavedComputer",stale);Object fresh=selected(activity);
        assertNotSame(first,fresh);assertTrue((Boolean)ReflectionHelpers.getField(first,"connecting"));
        assertEquals(stale.getId(),ReflectionHelpers.getField(fresh,"selectedSavedId"));
        assertEquals(2,((Number)invoke(workspace(activity),"getSize")).intValue());
        new ComputerBook(context).remove(stale.getId());
        invoke(activity,"openSavedComputer",stale);
        assertSame(fresh,selected(activity));assertEquals(2,((Number)invoke(workspace(activity),"getSize")).intValue());
        String corrupted="{broken-registry";
        assertTrue(context.getSharedPreferences("saved_computers",0).edit().putString("registry",corrupted).commit());
        invoke(activity,"openSavedComputer",stale);
        assertSame(fresh,selected(activity));assertEquals(2,((Number)invoke(workspace(activity),"getSize")).intValue());
        assertEquals(corrupted,context.getSharedPreferences("saved_computers",0).getString("registry",null));
    }

    @Test public void delayedAuthorizedCallbackCannotRecreateTheOldEndpointAfterAnotherSessionUpdatesTheCard() throws Exception {
        MainActivity activity=start().get();Object session=selected(activity);MeshPairing old=pairing(0),next=pairing(3);
        input(session,"privateCode").setText(old.getCode());invoke(session,"saveCurrentAccess");
        ComputerBook book=new ComputerBook(context);SavedComputer saved=book.entries().get(0);
        ReflectionHelpers.setField(session,"connectingSavedId",saved.getId());
        ReflectionHelpers.setField(session,"connectingEndpoint",new kotlin.Pair<>(old.getAddress(),old.getPort()));
        new ComputerBook(context).update(saved.getId(),saved.getName(),next.getAddress(),next.getPort());
        Peer oldConnection=new Peer(new Socket(){
            public InputStream getInputStream(){return new ByteArrayInputStream(new byte[0]);}
            public OutputStream getOutputStream(){return new ByteArrayOutputStream();}
        },message->kotlin.Unit.INSTANCE,reason->kotlin.Unit.INSTANCE,new PeerPolicy());
        try {
            invoke(session,"rememberConnectedComputer",oldConnection);
            assertEquals(1,book.entries().size());assertEquals(next.getAddress(),book.entries().get(0).getAddress());
            assertEquals(saved.getId(),book.entries().get(0).getId());
            new ComputerBook(context).remove(saved.getId());
            invoke(session,"rememberConnectedComputer",oldConnection);
            assertTrue(book.entries().isEmpty());
        } finally { oldConnection.close("fixture complete"); }
    }
}
