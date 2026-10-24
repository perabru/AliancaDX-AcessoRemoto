package qa;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;
import android.widget.Button;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import static org.junit.Assert.*;
import java.io.File;
import java.io.FileOutputStream;

@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class NativeCanvasSmokeTest {
    @Test public void nativeCanvasPaintsActualAndroidWidgets() throws Exception {
        Context context = RuntimeEnvironment.getApplication();
        Button button = new Button(context);
        button.setText("Native Android Canvas");
        button.setTextColor(Color.WHITE);
        button.setBackgroundColor(Color.rgb(0,122,80));
        button.measure(View.MeasureSpec.makeMeasureSpec(440, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(96, View.MeasureSpec.EXACTLY));
        button.layout(0, 0, 440, 96);
        Bitmap bitmap = Bitmap.createBitmap(440, 96, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        button.draw(canvas);
        assertTrue("Native graphics must paint pixels", bitmap.getPixel(4, 4) != 0);
        try (FileOutputStream out = new FileOutputStream(new File("output/native-smoke.png"))) {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, out));
        }
        bitmap.recycle();
    }
}
