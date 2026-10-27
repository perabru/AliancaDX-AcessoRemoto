package qa;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.*;
import android.os.Looper;
import br.com.perabru.minianydesk.*;
import kotlin.Unit;
import org.junit.*;
import org.junit.runner.RunWith;
import org.robolectric.*;
import org.robolectric.annotation.Config;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.util.ReflectionHelpers;
import javax.net.ssl.*;
import java.io.*;
import java.net.URL;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.util.*;
import static org.junit.Assert.*;

/** Controlled HTTPS responses and native PackageInstaller API contracts; no real-phone claim. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35)
public class AndroidInternalUpdateTest {
    static final byte[] BODY="signed APK fixture bytes".getBytes(java.nio.charset.StandardCharsets.UTF_8);
    final List<ActivityController<Activity>> activities=new ArrayList<>();
    final List<File> files=new ArrayList<>();
    @After public void cleanup(){AndroidInstallState.INSTANCE.setObserver(null);for(ActivityController<Activity> a:activities)a.pause().stop().destroy();for(File f:files)f.delete();}
    Activity activity(){ActivityController<Activity> a=Robolectric.buildActivity(Activity.class).setup().visible();activities.add(a);AndroidInstallState.INSTANCE.clear(a.get());return a.get();}
    static String sha(byte[] bytes)throws Exception{StringBuilder s=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))s.append(String.format(Locale.ROOT,"%02x",b));return s.toString();}
    static AndroidReleaseUpdate.Result release(byte[] bytes)throws Exception{return new AndroidReleaseUpdate.Result("7.5.0","7.6.0","v7.6.0","https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.6.0/AliancaDX_Acesso_Remoto_7.6.0.apk",AndroidReleaseUpdate.Relation.AVAILABLE,(long)bytes.length,sha(bytes));}
    File directory(Activity a){File d=new File(a.getCacheDir(),"qa-internal-update-"+System.nanoTime());assertTrue(d.mkdirs());files.add(d);return d;}
    static class Response extends HttpsURLConnection {
        byte[] bytes;int status=200;String location;long length;boolean disconnected;
        Response(URL url,byte[] bytes){super(url);this.bytes=bytes;length=bytes.length;}
        public void disconnect(){disconnected=true;}public boolean usingProxy(){return false;}public void connect(){}
        public int getResponseCode(){return status;}public long getContentLengthLong(){return length;}
        public String getHeaderField(String key){return key.equalsIgnoreCase("Location")?location:null;}
        public InputStream getInputStream(){return new ByteArrayInputStream(bytes);}
        public String getCipherSuite(){return "TLS_AES_256_GCM_SHA384";}public Certificate[] getLocalCertificates(){return null;}public Certificate[] getServerCertificates(){return null;}
    }
    @Test public void streamsOfficialAssetPrivatelyWithExactSizeDigestAndProgress()throws Exception{
        Activity a=activity();File dir=directory(a);File stale=new File(dir,"aliancadx-abandoned.apk");java.nio.file.Files.write(stale.toPath(),BODY);File unrelated=new File(dir,"keep.txt");java.nio.file.Files.write(unrelated.toPath(),BODY);files.add(unrelated);List<Response> responses=new ArrayList<>();List<Long> progress=new ArrayList<>();
        AndroidApkDownload source=new AndroidApkDownload(url->{Response r=new Response(url,BODY);responses.add(r);return r;});
        File result=source.download(release(BODY),dir,(got,total)->{assertEquals((long)BODY.length,(long)total);progress.add(got);return Unit.INSTANCE;});files.add(result);
        assertFalse("An interrupted previous private download must be cleaned",stale.exists());assertTrue("Only updater-owned temporary names may be cleaned",unrelated.exists());assertEquals(dir.getCanonicalFile(),result.getParentFile().getCanonicalFile());assertArrayEquals(BODY,java.nio.file.Files.readAllBytes(result.toPath()));assertEquals(Arrays.asList((long)BODY.length),progress);assertTrue(responses.get(0).disconnected);assertFalse(responses.get(0).getInstanceFollowRedirects());
    }
    @Test public void allowsOnlyBoundedHttpsAssetRedirectsAndDisconnectsEveryHop()throws Exception{
        Activity a=activity();List<Response> responses=new ArrayList<>();AndroidApkDownload source=new AndroidApkDownload(url->{Response r=new Response(url,BODY);if(responses.isEmpty()){r.status=302;r.location="https://release-assets.githubusercontent.com/github-production-release-asset/fixture?token=abc";}responses.add(r);return r;});
        File result=source.download(release(BODY),directory(a),(g,t)->Unit.INSTANCE);files.add(result);assertEquals(2,responses.size());for(Response r:responses)assertTrue(r.disconnected);
        for(String bad:new String[]{"http://release-assets.githubusercontent.com/file","https://release-assets.githubusercontent.com.evil.test/file","https://user@objects.githubusercontent.com/file","https://objects.githubusercontent.com:444/file","https://objects.githubusercontent.com/file#fragment","https://example.com/file"})assertFalse(AndroidApkDownload.Companion.allowedAssetRedirect(new URL(bad)));
    }
    @Test public void truncatedOversizedChangedAndCancelledDownloadsNeverLeaveAnApk()throws Exception{
        Activity a=activity();
        for(int kind=0;kind<4;kind++){
            File dir=directory(a);final int mode=kind;AndroidApkDownload source=new AndroidApkDownload(url->{Response r=new Response(url,mode==0?new byte[]{1}:mode==1?new byte[BODY.length+1]:mode==2?new byte[BODY.length]:BODY);r.length=-1;return r;});if(mode==3)source.cancel();
            try{source.download(release(BODY),dir,(g,t)->Unit.INSTANCE);fail("bad download accepted "+kind);}catch(Exception expected){}
            assertEquals("Partial APK must be removed, case "+kind,0,Objects.requireNonNull(dir.list()).length);
        }
    }
    @Test public void missingDigestWrongRoleAndOldVersionNeverStartNetwork()throws Exception{
        Activity a=activity();final int[] network={0};AndroidApkDownload source=new AndroidApkDownload(url->{network[0]++;return new Response(url,BODY);});
        for(AndroidReleaseUpdate.Result r:new AndroidReleaseUpdate.Result[]{new AndroidReleaseUpdate.Result("7.5.0","7.6.0","v7.6.0",release(BODY).getApkUrl(),AndroidReleaseUpdate.Relation.AVAILABLE),new AndroidReleaseUpdate.Result("7.5.0","7.6.0","v7.6.0",release(BODY).getApkUrl().replace(".apk",".exe"),AndroidReleaseUpdate.Relation.AVAILABLE,(long)BODY.length,sha(BODY)),new AndroidReleaseUpdate.Result("7.5.0","7.5.0","v7.5.0",release(BODY).getApkUrl(),AndroidReleaseUpdate.Relation.CURRENT,(long)BODY.length,sha(BODY))}){
            try{source.download(r,directory(a),(g,t)->Unit.INSTANCE);fail("Invalid update accepted");}catch(IllegalArgumentException expected){}
        }assertEquals(0,network[0]);
    }
    static SigningInfo signer(String certificate){SigningInfo info=new SigningInfo();Shadows.shadowOf(info).setSignatures(new Signature[]{new Signature(certificate.getBytes())});return info;}
    File packageFixture(Activity a,String packageName,String version,int versionCode,String certificate)throws Exception{
        File f=File.createTempFile("qa-update-",".apk",a.getCacheDir());java.nio.file.Files.write(f.toPath(),BODY);files.add(f);
        PackageInfo installed=new PackageInfo();installed.packageName=a.getPackageName();installed.versionName="7.5.0";installed.setLongVersionCode(70500);installed.signingInfo=signer("owner");installed.applicationInfo=new android.content.pm.ApplicationInfo();installed.applicationInfo.packageName=a.getPackageName();Shadows.shadowOf(a.getPackageManager()).installPackage(installed);
        PackageInfo archive=new PackageInfo();archive.packageName=packageName;archive.versionName=version;archive.setLongVersionCode(versionCode);archive.signingInfo=signer(certificate);Shadows.shadowOf(a.getPackageManager()).setPackageArchiveInfo(f.getAbsolutePath(),archive);return f;
    }
    @Test public void verifiesOwnPackageNewerVersionSignerAndHashBeforeCommitting()throws Exception{
        Activity a=activity();AndroidApkInstaller installer=new AndroidApkInstaller(a);File good=packageFixture(a,a.getPackageName(),"7.6.0",70600,"owner");installer.verify(good,release(BODY));
        for(File bad:new File[]{packageFixture(a,"other.app","7.6.0",70600,"owner"),packageFixture(a,a.getPackageName(),"7.6.0",70500,"owner"),packageFixture(a,a.getPackageName(),"7.7.0",70700,"owner"),packageFixture(a,a.getPackageName(),"7.6.0",70600,"attacker")}){
            try{installer.verify(bad,release(BODY));fail("Invalid package accepted");}catch(IllegalArgumentException expected){}
        }
        java.nio.file.Files.write(good.toPath(),new byte[BODY.length]);try{installer.verify(good,release(BODY));fail("Modified APK accepted");}catch(IllegalArgumentException expected){}
        assertTrue(a.getPackageManager().getPackageInstaller().getMySessions().isEmpty());
    }
    @Test public void installerRequiresSourcePermissionAndUsesOwnPackageSession()throws Exception{
        Activity a=activity();File apk=packageFixture(a,a.getPackageName(),"7.6.0",70600,"owner");AndroidApkInstaller installer=new AndroidApkInstaller(a);
        Shadows.shadowOf(a.getPackageManager()).setCanRequestPackageInstalls(false);try{installer.commit(apk,release(BODY));fail("Missing permission accepted");}catch(IllegalStateException expected){}assertTrue(a.getPackageManager().getPackageInstaller().getMySessions().isEmpty());
        Shadows.shadowOf(a.getPackageManager()).setCanRequestPackageInstalls(true);int id=installer.commit(apk,release(BODY));PackageInstaller.SessionInfo session=a.getPackageManager().getPackageInstaller().getSessionInfo(id);assertNotNull(session);assertEquals(a.getPackageName(),session.getAppPackageName());assertTrue(apk.isFile());
    }
    @Test public void recoveryAbandonsLostConfirmationAndUncommittedSessionsAndClearsCompletedVersion()throws Exception{
        Activity a=activity();PackageInstaller installer=a.getPackageManager().getPackageInstaller();int id=installer.createSession(new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL));AndroidInstallState.INSTANCE.begin(a,id,"7.6.0");
        assertTrue(AndroidInstallState.INSTANCE.restore(a,"7.5.0").contains("interrompida"));assertFalse(AndroidInstallState.INSTANCE.hasSession(a));assertNull(installer.getSessionInfo(id));
        id=installer.createSession(new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL));AndroidInstallState.INSTANCE.begin(a,id,"7.6.0");AndroidInstallState.INSTANCE.received(a,PackageInstaller.STATUS_PENDING_USER_ACTION,null);assertTrue(AndroidInstallState.INSTANCE.restore(a,"7.5.0").contains("confirmação"));assertFalse(AndroidInstallState.INSTANCE.hasSession(a));
        AndroidInstallState.INSTANCE.begin(a,99,"7.6.0");assertNull(AndroidInstallState.INSTANCE.restore(a,"7.6.0"));assertFalse(AndroidInstallState.INSTANCE.hasSession(a));
    }
    @Test public void receiverRejectsOtherSessionsAndRetainsSystemConfirmationOrReadableErrors()throws Exception{
        Activity a=activity();AndroidInstallState.INSTANCE.begin(a,42,"7.6.0");AndroidInstallReceiver receiver=new AndroidInstallReceiver();
        Intent callback=new Intent(a.getPackageName()+".UPDATE_RESULT").putExtra(PackageInstaller.EXTRA_SESSION_ID,43).putExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_SUCCESS);receiver.onReceive(a,callback);assertEquals(Integer.MIN_VALUE,AndroidInstallState.INSTANCE.status(a));
        Intent confirmation=new Intent("system.fixture.CONFIRM");callback.putExtra(PackageInstaller.EXTRA_SESSION_ID,42).putExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_PENDING_USER_ACTION).putExtra(Intent.EXTRA_INTENT,confirmation);receiver.onReceive(a,callback);assertEquals(PackageInstaller.STATUS_PENDING_USER_ACTION,AndroidInstallState.INSTANCE.status(a));assertEquals("system.fixture.CONFIRM",AndroidInstallState.INSTANCE.takeConfirmation().getAction());assertNull(AndroidInstallState.INSTANCE.takeConfirmation());
        callback.putExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE_STORAGE);receiver.onReceive(a,callback);assertTrue(AndroidInstallState.INSTANCE.failureMessage(AndroidInstallState.INSTANCE.status(a)).contains("espaço"));
    }
}
