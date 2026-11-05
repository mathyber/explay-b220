package go1;

/* Files on the memory card through the host (GO1 loader on the phone).
   Paths look like "E:\\JAVA\\RMS\\GAME\\scores.rms". Handles are ints, 0 = failed. */
public final class Files {
    public static final int READ = 0, CREATE = 1, UPDATE = 2, APPEND = 3;

    private Files() {}

    public static native int open(String path, int mode);
    /* Bytes read or written, -1 on error. */
    public static native int read(int file, byte[] b, int offset, int length);
    public static native int write(int file, byte[] b, int offset, int length);
    /* 0 on success. */
    public static native int seek(int file, int position);
    public static native int size(int file);
    public static native void close(int file);
    public static native boolean remove(String path);
    /* True when created or already there. */
    public static native boolean makeDir(String path);

    /* The whole file, or null when it cannot be read. */
    public static byte[] readAll(String path) {
        int f = open(path, READ);
        if (f == 0) return null;
        try {
            int size = size(f);
            if (size < 0) return null;
            byte[] data = new byte[size];
            int done = 0;
            while (done < size) {
                int n = read(f, data, done, size-done);
                if (n <= 0) return null;
                done += n;
            }
            return data;
        } finally {
            close(f);
        }
    }

    /* Replaces the file; false when it could not be written completely. */
    public static boolean writeAll(String path, byte[] data, int length) {
        int f = open(path, CREATE);
        if (f == 0) return false;
        try {
            int done = 0;
            while (done < length) {
                int n = write(f, data, done, length-done);
                if (n <= 0) return false;
                done += n;
            }
            return true;
        } finally {
            close(f);
        }
    }

    /* Creates every folder of "E:\\A\\B\\C" that is missing. */
    public static void makeDirs(String path) {
        int at = path.indexOf('\\', 3);
        while (at > 0) {
            makeDir(path.substring(0, at));
            at = path.indexOf('\\', at+1);
        }
        makeDir(path);
    }

    /* A file or folder name from any text: letters, digits and _ only, at most 24 characters. */
    public static String safeName(String name) {
        StringBuffer out = new StringBuffer();
        for (int i = 0; i < name.length() && out.length() < 24; i++) {
            char c = name.charAt(i);
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) out.append(c);
            else if (out.length() > 0 && out.charAt(out.length()-1) != '_') out.append('_');
        }
        return out.length() > 0 ? out.toString() : "_";
    }
}
