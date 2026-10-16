import java.sql.*;

/** Reads a Heliad index the way Studio's Add Layer tab would: read-only, while Heliad writes. */
public class ReadIndex {
    public static void main(String[] args) throws Exception {
        String url = "jdbc:sqlite:file:" + args[0] + "?mode=ro";
        int rounds = Integer.parseInt(args[1]);
        try (Connection c = DriverManager.getConnection(url)) {
            try (Statement s = c.createStatement()) { s.execute("PRAGMA busy_timeout = 5000"); }
            int last = -1, changes = 0;
            for (int i = 0; i < rounds; i++) {
                try (Statement s = c.createStatement();
                     ResultSet rs = s.executeQuery("SELECT COUNT(*) FROM current_files")) {
                    rs.next();
                    int n = rs.getInt(1);
                    if (n < last) throw new IllegalStateException("count went backwards: " + last + " -> " + n);
                    if (n != last) changes++;
                    last = n;
                }
                Thread.sleep(10);
            }
            try (Statement s = c.createStatement();
                 ResultSet rs = s.executeQuery("SELECT label, COUNT(*) FROM current_files GROUP BY label")) {
                while (rs.next()) System.out.println(rs.getString(1) + "\t" + rs.getInt(2));
            }
            try (Statement s = c.createStatement()) {
                s.execute("DELETE FROM files");
                System.out.println("ERROR: write succeeded on a read-only connection");
                System.exit(2);
            } catch (SQLException expected) {
                System.out.println("write refused as expected");
            }
            System.out.println("final count " + last + ", distinct counts seen " + changes);
        }
    }
}
