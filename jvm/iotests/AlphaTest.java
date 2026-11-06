import java.util.Random;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* Images with binary alpha (0/255) and with partial alpha, drawn whole, as regions
   with every transform, through region copies, and read back with getRGB:
   one checksum line per case. */
public class AlphaTest {
    static int sum(int[] a) { int s = 0; for (int i = 0; i < a.length; i++) s = s * 31 + a[i]; return s; }

    public static void main(String[] args) {
        Random rnd = new Random(11);
        Image[] sources = new Image[4];
        for (int k = 0; k < 4; k++) {
            int w = 5 + k * 6, h = 4 + k * 3;
            int[] rgb = new int[w * h];
            for (int i = 0; i < rgb.length; i++) {
                int a = k < 2 ? ((rnd.nextInt(3) == 0) ? 0 : 0xff) : (i % 3 == 0 ? 0x40 : (i % 3 == 1 ? 0 : 0xff));
                rgb[i] = a << 24 | rnd.nextInt() & 0xffffff;
            }
            sources[k] = Image.createRGBImage(rgb, w, h, true);
            int[] back = new int[w * h];
            sources[k].getRGB(back, 0, w, 0, 0, w, h);
            System.out.println("getRGB " + k + " " + sum(back));
            Image part = Image.createImage(sources[k], 1, 1, w - 2, h - 2, k * 2 + 1);
            int[] pb = new int[part.getWidth() * part.getHeight()];
            part.getRGB(pb, 0, part.getWidth(), 0, 0, part.getWidth(), part.getHeight());
            System.out.println("region " + k + " " + sum(pb));
        }
        Image target = Image.createImage(30, 24);
        Graphics g = target.getGraphics();
        int[] out = new int[30 * 24];
        for (int n = 0; n < 200; n++) {
            g.setClip(0, 0, 30, 24);
            g.setColor(0x224466);
            g.fillRect(0, 0, 30, 24);
            Image s = sources[n % 4];
            int w = 1 + rnd.nextInt(s.getWidth()), h = 1 + rnd.nextInt(s.getHeight());
            int sx = rnd.nextInt(s.getWidth() - w + 1), sy = rnd.nextInt(s.getHeight() - h + 1);
            g.setClip(rnd.nextInt(10) - 2, rnd.nextInt(8) - 2, rnd.nextInt(30), rnd.nextInt(24));
            if (n % 3 == 0) g.drawImage(s, rnd.nextInt(30) - 5, rnd.nextInt(24) - 5, Graphics.TOP | Graphics.LEFT);
            else g.drawRegion(s, sx, sy, w, h, rnd.nextInt(8), rnd.nextInt(30) - 5, rnd.nextInt(24) - 5, Graphics.TOP | Graphics.LEFT);
            target.getRGB(out, 0, 30, 0, 0, 30, 24);
            System.out.println(n + " " + sum(out));
        }
    }
}
