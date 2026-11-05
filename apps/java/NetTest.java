import java.io.InputStream;
import javax.microedition.io.Connector;
import javax.microedition.io.HttpConnection;
import javax.microedition.rms.RecordStore;

/* Java network and record store check for the phone: an HTTP GET through
   javax.microedition.io and a launch counter saved on the card. */
public class NetTest {
    public static void main(String[] args) throws Exception {
        RecordStore store = RecordStore.openRecordStore("runs", true);
        int runs = store.getNumRecords()+1;
        store.addRecord(new byte[] {(byte) runs}, 0, 1);
        store.closeRecordStore();
        System.out.println("run " + runs);
        long start = System.currentTimeMillis();
        HttpConnection c = (HttpConnection) Connector.open("http://info.cern.ch/");
        System.out.println("HTTP " + c.getResponseCode());
        System.out.println(c.getType());
        InputStream in = c.openInputStream();
        byte[] buffer = new byte[256];
        int total = 0, n;
        StringBuffer title = new StringBuffer();
        while ((n = in.read(buffer, 0, buffer.length)) > 0) {
            for (int i = 0; i < n && title.length() < 26 && total == 0; i++)
                if (buffer[i] >= 32 && buffer[i] < 127) title.append((char) buffer[i]);
            total += n;
        }
        c.close();
        System.out.println(total + " bytes, " + (System.currentTimeMillis()-start) + " ms");
        System.out.println(title.toString());
    }
}
