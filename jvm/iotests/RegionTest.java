import java.util.Random;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* drawRegion with every transform, clipped and partly outside, with source alpha:
   one checksum line per case. */
public class RegionTest {
    public static void main(String[] args) {
        Random rnd = new Random(42);
        int[] rgb = new int[9*7];
        for (int i = 0; i < rgb.length; i++) {
            int a = i%5 == 0 ? 0 : i%5 == 1 ? 0x80 : 0xff;
            rgb[i] = a << 24 | rnd.nextInt() & 0xffffff;
        }
        Image alpha = Image.createRGBImage(rgb, 9, 7, true), solid = Image.createRGBImage(rgb, 9, 7, false);
        Image target = Image.createImage(20, 16);
        Graphics g = target.getGraphics();
        int[] out = new int[20*16];
        for (int n = 0; n < 300; n++) {
            g.setClip(0, 0, 20, 16);
            g.setColor(0x336699);
            g.fillRect(0, 0, 20, 16);
            int w = 1+rnd.nextInt(9), h = 1+rnd.nextInt(7);
            int sx = rnd.nextInt(10-w), sy = rnd.nextInt(8-h);
            g.setClip(rnd.nextInt(12)-2, rnd.nextInt(10)-2, rnd.nextInt(20), rnd.nextInt(16));
            g.drawRegion(n%2 == 0 ? alpha : solid, sx, sy, w, h, rnd.nextInt(8), rnd.nextInt(26)-6, rnd.nextInt(22)-6,
                         Graphics.TOP | Graphics.LEFT);
            target.getRGB(out, 0, 20, 0, 0, 20, 16);
            int sum = 0;
            for (int i = 0; i < out.length; i++) sum = sum*31+out[i];
            System.out.println(sum);
        }
    }
}
