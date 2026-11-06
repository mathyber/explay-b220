import java.util.*;

/* Library code made hot (jit.c compiles it): strings, buffers, numbers, collections. */
public class JitTest3 {
    public static void main(String[] args) {
        StringBuffer all = new StringBuffer();
        int hash = 0;
        for (int i = -500; i < 1500; i++) {
            String s = "v" + i + ":" + (i * 7919) + "/" + Integer.toHexString(i) + "," + Long.toString(i * 100000007L) + ";" + (char) ('a' + (i & 15));
            hash = hash * 31 + s.hashCode();
            if (i % 250 == 0) System.out.println(s);
            all.append(s.substring(1, 3)).append(s.indexOf(':')).append(s.lastIndexOf('/'));
            if (s.startsWith("v1") && s.endsWith("c")) hash ^= s.length();
            hash += s.toUpperCase().charAt(0) + s.trim().length() + (s.equals("v5") ? 1 : 0);
            hash += Integer.parseInt(String.valueOf(i)) + s.compareTo("v3");
        }
        System.out.println("hash " + hash + " len " + all.length() + " " + all.toString().hashCode());
        Vector v = new Vector();
        Hashtable h = new Hashtable();
        for (int i = 0; i < 3000; i++) {
            v.addElement(new Integer(i * 3));
            h.put("k" + (i % 500), new Integer(i));
        }
        int sum = 0;
        for (int i = 0; i < v.size(); i += 7) sum += ((Integer) v.elementAt(i)).intValue();
        for (Enumeration e = h.keys(); e.hasMoreElements();) sum += ((Integer) h.get(e.nextElement())).intValue();
        System.out.println("collections " + sum + " " + v.indexOf(new Integer(2997)) + " " + h.size());
        Random r = new Random(42);
        int rs = 0;
        for (int i = 0; i < 5000; i++) rs = rs * 17 + r.nextInt() + (r.nextInt() >>> 20);
        System.out.println("random " + rs);
        char[] chars = new char[2000];
        for (int i = 0; i < chars.length; i++) chars[i] = (char) ('A' + i % 26);
        String big = new String(chars);
        byte[] bytes = big.getBytes();
        String back = new String(bytes, 3, 1500);
        System.out.println("strings " + big.hashCode() + " " + back.hashCode() + " " + big.indexOf("XYZ", 100) + " " + back.replace('A', 'z').hashCode());
        int[] src = new int[1000], dst = new int[1000];
        for (int i = 0; i < 1000; i++) src[i] = i * i;
        for (int k = 0; k < 50; k++) System.arraycopy(src, k, dst, 1000 - k - 900, 900);
        int ds = 0;
        for (int i = 0; i < 1000; i++) ds += dst[i] ^ i;
        System.out.println("copy " + ds + " " + Math.abs(-5) + " " + Math.max(3, 9));
        System.out.println("[finished]");
    }
}
