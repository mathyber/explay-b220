import java.util.Random;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* drawRegion of wide images without rotation, at every alignment of source and
   destination: opaque, a bit of alpha per pixel (runs of 0 and 255) and byte
   alpha. One checksum line per case. */
public class BlitTest {
    public static void main(String[] args) {
        Random rnd = new Random(7);
        int w = 100, h = 40;
        int[] bits = new int[w*h], bytes = new int[w*h];
        for (int i = 0; i < bits.length; i++) {
            int c = rnd.nextInt() & 0xffffff;
            int run = (i / (1 + (i % 13))) % 4;
            bits[i] = (run == 0 ? 0 : 0xff) << 24 | c;
            bytes[i] = (i % 7 == 0 ? 0x80 : run == 0 ? 0 : 0xff) << 24 | c;
        }
        Image[] images = {Image.createRGBImage(bits, w, h, false), Image.createRGBImage(bits, w, h, true),
                          Image.createRGBImage(bytes, w, h, true)};
        Image target = Image.createImage(176, 60);
        Graphics g = target.getGraphics();
        int[] out = new int[176*60];
        for (int n = 0; n < 240; n++) {
            g.setClip(0, 0, 176, 60);
            g.setColor(0x204060 + n);
            g.fillRect(0, 0, 176, 60);
            int rw = 1 + rnd.nextInt(w), rh = 1 + rnd.nextInt(h);
            int sx = rnd.nextInt(w - rw + 1), sy = rnd.nextInt(h - rh + 1);
            if (n % 4 == 0) g.setClip(rnd.nextInt(30), rnd.nextInt(20), 40 + rnd.nextInt(130), 10 + rnd.nextInt(50));
            g.drawRegion(images[n % 3], sx, sy, rw, rh, 0, rnd.nextInt(120) - 20, rnd.nextInt(40) - 10, Graphics.TOP | Graphics.LEFT);
            target.getRGB(out, 0, 176, 0, 0, 176, 60);
            int sum = 0;
            for (int i = 0; i < out.length; i++) sum = sum * 31 + out[i];
            System.out.println(sum);
        }
        System.out.println("[finished]");
    }
}
