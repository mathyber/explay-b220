package javax.microedition.io.file;

import java.io.IOException;
import java.io.OutputStream;
import java.util.Enumeration;
import javax.microedition.io.StreamConnection;

/* JSR-75 file access (interface only; Connector does not open file URLs yet). */
public interface FileConnection extends StreamConnection {
    boolean isOpen();
    OutputStream openOutputStream(long byteOffset) throws IOException;
    long totalSize();
    long availableSize();
    long usedSize();
    long directorySize(boolean includeSubDirs) throws IOException;
    long fileSize() throws IOException;
    boolean canRead();
    boolean canWrite();
    boolean isHidden();
    void setReadable(boolean readable) throws IOException;
    void setWritable(boolean writable) throws IOException;
    void setHidden(boolean hidden) throws IOException;
    Enumeration list() throws IOException;
    Enumeration list(String filter, boolean includeHidden) throws IOException;
    void create() throws IOException;
    void mkdir() throws IOException;
    boolean exists();
    boolean isDirectory();
    void delete() throws IOException;
    void rename(String newName) throws IOException;
    void truncate(long byteOffset) throws IOException;
    void setFileConnection(String fileName) throws IOException;
    String getName();
    String getPath();
    String getURL();
    long lastModified();
}
