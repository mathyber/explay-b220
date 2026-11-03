package javax.microedition.rms;

public interface RecordListener {
    void recordAdded(RecordStore store, int id);
    void recordChanged(RecordStore store, int id);
    void recordDeleted(RecordStore store, int id);
}
