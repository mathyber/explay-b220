import com.nokia.mid.ui.DirectGraphics;
import com.nokia.mid.ui.DirectUtils;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;
import javax.microedition.media.Manager;
import javax.microedition.media.Player;
import javax.microedition.media.control.VolumeControl;
import javax.microedition.rms.InvalidRecordIDException;
import javax.microedition.rms.RecordComparator;
import javax.microedition.rms.RecordEnumeration;
import javax.microedition.rms.RecordStore;
import javax.microedition.rms.RecordStoreNotFoundException;

/* RMS, silent MMAPI players and Nokia DirectGraphics, printed for tests.test_jvm. */
public class PlatformTest {
    public static void main(String[] args) throws Exception {
        records();
        media();
        direct();
    }

    static void records() throws Exception {
        try {
            RecordStore.openRecordStore("missing", false);
        } catch (RecordStoreNotFoundException e) {
            System.out.println("not found: " + e.getMessage());
        }
        RecordStore store = RecordStore.openRecordStore("scores", true);
        int a = store.addRecord(new byte[] {3, 1}, 0, 2);
        int b = store.addRecord(new byte[] {9, 1, 2}, 1, 2);
        int c = store.addRecord(new byte[] {7}, 0, 1);
        store.setRecord(a, new byte[] {5}, 0, 1);
        store.deleteRecord(c);
        System.out.println("ids " + a + " " + b + " records " + store.getNumRecords() + " next " + store.getNextRecordID());
        byte[] first = store.getRecord(a), second = store.getRecord(b);
        System.out.println("first " + first[0] + " second " + second[0] + "," + second[1]);
        try {
            store.getRecord(c);
        } catch (InvalidRecordIDException e) {
            System.out.println("deleted " + e.getMessage());
        }
        RecordEnumeration e = store.enumerateRecords(null, new RecordComparator() {
            public int compare(byte[] x, byte[] y) {
                return x[0] < y[0] ? PRECEDES : x[0] > y[0] ? FOLLOWS : EQUIVALENT;
            }
        }, false);
        StringBuffer order = new StringBuffer("sorted");
        while (e.hasNextElement()) order.append(' ').append(e.nextRecordId());
        System.out.println(order);
        store.closeRecordStore();
        RecordStore again = RecordStore.openRecordStore("scores", false);
        System.out.println("reopened " + again.getNumRecords() + " stores " + RecordStore.listRecordStores().length);
        again.closeRecordStore();
        RecordStore.deleteRecordStore("scores");
        System.out.println("after delete " + RecordStore.listRecordStores());
    }

    static void media() throws Exception {
        Player p = Manager.createPlayer(new java.io.ByteArrayInputStream(new byte[] {'M', 'T', 'h', 'd'}), "audio/midi");
        System.out.println("state " + p.getState());
        p.realize();
        p.prefetch();
        p.setLoopCount(-1);
        VolumeControl volume = (VolumeControl) p.getControl("VolumeControl");
        System.out.println("volume " + volume.setLevel(150));
        p.start();
        System.out.println("started " + (p.getState() == Player.STARTED));
        p.stop();
        System.out.println("stopped " + p.getState() + " " + p.getContentType());
        p.close();
        System.out.println("closed " + p.getState());
    }

    /* A 3x2 image with six colours drawn with each manipulation, read back row by row. */
    static void direct() {
        int[] colours = {0xff0000, 0x00ff00, 0x0000ff, 0xffff00, 0x00ffff, 0xff00ff};
        Image source = Image.createRGBImage(withAlpha(colours), 3, 2, false);
        int[] manipulations = {0, DirectGraphics.FLIP_HORIZONTAL, DirectGraphics.FLIP_VERTICAL,
            DirectGraphics.FLIP_HORIZONTAL | DirectGraphics.FLIP_VERTICAL,
            DirectGraphics.ROTATE_90, DirectGraphics.ROTATE_180, DirectGraphics.ROTATE_270};
        for (int i = 0; i < manipulations.length; i++) {
            Image target = Image.createImage(3, 3);
            DirectGraphics dg = DirectUtils.getDirectGraphics(target.getGraphics());
            dg.drawImage(source, 0, 0, Graphics.TOP | Graphics.LEFT, manipulations[i]);
            boolean turned = manipulations[i] == DirectGraphics.ROTATE_90 || manipulations[i] == DirectGraphics.ROTATE_270;
            int w = turned ? 2 : 3, h = turned ? 3 : 2;
            int[] out = new int[w * h];
            target.getRGB(out, 0, w, 0, 0, w, h);
            StringBuffer line = new StringBuffer("m" + manipulations[i]);
            for (int j = 0; j < out.length; j++) line.append(' ').append(index(colours, out[j] & 0xffffff));
            System.out.println(line);
        }
    }

    static int[] withAlpha(int[] rgb) {
        int[] out = new int[rgb.length];
        for (int i = 0; i < rgb.length; i++) out[i] = 0xff000000 | rgb[i];
        return out;
    }

    /* Colour index, tolerant of RGB565 rounding. */
    static int index(int[] colours, int rgb) {
        for (int i = 0; i < colours.length; i++) {
            int c = colours[i];
            if (((c >> 16) & 0x80) == ((rgb >> 16) & 0x80) && ((c >> 8) & 0x80) == ((rgb >> 8) & 0x80) && (c & 0x80) == (rgb & 0x80)) return i;
        }
        return -1;
    }
}
