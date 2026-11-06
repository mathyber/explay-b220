import java.io.InputStream;
import javax.microedition.lcdui.Image;

/* Decodes /p0.png, /p1.png, ... and prints a checksum of getRGB per image. */
public class PngTest {
    public static void main(String[] args) throws Exception {
        for (int i = 0; ; i++) {
            InputStream in = PngTest.class.getResourceAsStream("/p" + i + ".png");
            if (in == null) break;
            Image img;
            try { img = Image.createImage(in); } catch (Exception e) { System.out.println(i + " error " + e); continue; }
            int w = img.getWidth(), h = img.getHeight();
            int[] rgb = new int[w * h];
            img.getRGB(rgb, 0, w, 0, 0, w, h);
            int s = 0;
            for (int k = 0; k < rgb.length; k++) s = s * 31 + rgb[k];
            System.out.println(i + " " + w + "x" + h + " " + s);
        }
    }
}
