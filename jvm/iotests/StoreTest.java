import javax.microedition.io.Connector;
import javax.microedition.rms.RecordStore;

/* Run twice with the same card folder: the first run saves records,
   the second finds them again (record stores live on the card). */
public class StoreTest {
    public static void main(String[] args) throws Exception {
        RecordStore store = RecordStore.openRecordStore("scores", true);
        if (store.getNumRecords() == 0) {
            store.addRecord("one".getBytes(), 0, 3);
            int id = store.addRecord("two".getBytes(), 0, 3);
            store.setRecord(id, "TWO!".getBytes(), 0, 4);
            System.out.println("saved " + store.getNumRecords() + " next " + store.getNextRecordID());
            RecordStore.openRecordStore("temporary", true).closeRecordStore();
            RecordStore.deleteRecordStore("temporary");
        } else {
            StringBuffer s = new StringBuffer("loaded");
            for (int id = 1; id < store.getNextRecordID(); id++) s.append(' ').append(new String(store.getRecord(id)));
            System.out.println(s + " next " + store.getNextRecordID());
            String[] names = RecordStore.listRecordStores();
            System.out.println("stores " + names.length + " " + names[0]);
        }
        store.closeRecordStore();
        try {
            Connector.open("socket://example.org:80");
            System.out.println("network?");
        } catch (java.io.IOException e) {
            System.out.println("no network: " + e.getMessage());
        }
        try {
            Connector.open("https://example.org/");
        } catch (javax.microedition.io.ConnectionNotFoundException e) {
            System.out.println("https: " + e.getMessage());
        }
    }
}
