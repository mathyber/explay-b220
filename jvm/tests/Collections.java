import java.util.Enumeration;
import java.util.Hashtable;
import java.util.NoSuchElementException;
import java.util.Random;
import java.util.Stack;
import java.util.Vector;

/* java.util containers and Random; avoids hash iteration order. */
public class Collections {
    public static void main(String[] args) {
        Vector v = new Vector(2);
        for (int i = 0; i < 6; i++) v.addElement("item" + i);
        v.insertElementAt("first", 0);
        v.removeElementAt(3);
        v.removeElement("item4");
        v.setElementAt("changed", 1);
        System.out.println(v + " " + v.size() + " " + v.indexOf("item5") + " " + v.contains("item2"));
        StringBuffer seen = new StringBuffer();
        for (Enumeration e = v.elements(); e.hasMoreElements(); ) seen.append(((String) e.nextElement()).charAt(0));
        System.out.println(seen);
        try {
            new Vector().firstElement();
        } catch (NoSuchElementException e) {
            System.out.println("empty vector");
        }

        Stack s = new Stack();
        s.push("a");
        s.push("b");
        s.push("c");
        System.out.println(s.pop() + "" + s.peek() + " " + s.search("a") + " " + s.empty());

        Hashtable h = new Hashtable();
        for (int i = 0; i < 100; i++) h.put("k" + i, new Integer(i * i));
        h.put("k5", "five");
        h.remove("k7");
        int sum = 0;
        for (Enumeration e = h.elements(); e.hasMoreElements(); ) {
            Object o = e.nextElement();
            if (o instanceof Integer) sum += ((Integer) o).intValue();
        }
        System.out.println(h.size() + " " + h.get("k5") + " " + h.get("k9") + " " + h.get("k7") + " "
            + h.containsKey("k99") + " " + sum);

        Random r = new Random(42);
        System.out.println(r.nextInt() + " " + r.nextInt(100) + " " + r.nextInt(64) + " " + r.nextLong()
            + " " + r.nextBoolean() + " " + (int) (r.nextDouble() * 1000000) + " " + (int) (r.nextFloat() * 1000000));
    }
}
