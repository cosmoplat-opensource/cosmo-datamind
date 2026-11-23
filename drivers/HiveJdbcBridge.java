import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Types;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * HiveServer2 (Apache Hive or Spark Thrift Server) bridge for DataMind (DR-163).
 * Request on stdin, one field per line: mode (query|tables|selftest), JDBC URL, user, password, row limit,
 * then the SQL text. The password is never passed as a process argument. One JSON object is written to stdout;
 * failures are reported as {"ok": false, "stage": ..., "error_class": ..., "message": ...} with exit status 0.
 */
class HiveJdbcBridge {
    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String mode = line(in), url = line(in), user = line(in), password = line(in), limitText = line(in);
        StringBuilder sql = new StringBuilder();
        for (String l; (l = in.readLine()) != null; ) sql.append(sql.length() == 0 ? "" : "\n").append(l);
        if ("selftest".equals(mode)) {
            System.out.println("{\"ok\":true,\"text\":" + str("quote\" backslash\\ tab\t newline\n 控制\u0001 中文")
                    + ",\"database\":" + str(database(url)) + "}");
            return;
        }
        int limit = 500;
        try { limit = Math.max(1, Math.min(5000, Integer.parseInt(limitText.trim()))); } catch (NumberFormatException ignored) { }
        String stage = "driver";
        try {
            Class.forName("org.apache.hive.jdbc.HiveDriver");
            stage = "connect";
            DriverManager.setLoginTimeout(15);
            try (Connection db = DriverManager.getConnection(url, user, password.isEmpty() ? null : password)) {
                DatabaseMetaData meta = db.getMetaData();
                String head = "{\"ok\":true,\"product\":" + str(meta.getDatabaseProductName())
                        + ",\"version\":" + str(meta.getDatabaseProductVersion());
                if ("tables".equals(mode)) {
                    stage = "tables";
                    System.out.println(head + ",\"tables\":" + tables(db, meta, database(url), limit) + "}");
                } else {
                    stage = "query";
                    System.out.println(head + "," + query(db, sql.toString(), limit) + "}");
                }
            }
        } catch (ClassNotFoundException e) {
            fail("driver", e);
        } catch (SQLException | RuntimeException e) {
            fail(stage, e);
        }
    }

    private static String line(BufferedReader in) throws Exception {
        String l = in.readLine();
        return l == null ? "" : l;
    }

    private static String query(Connection db, String sql, int limit) throws SQLException {
        try (Statement st = db.createStatement()) {
            st.setMaxRows(limit + 1);
            try { st.setQueryTimeout(50); } catch (SQLException ignored) { }   // some HiveServer2 builds do not support it
            try (ResultSet rs = st.executeQuery(sql)) {
                ResultSetMetaData md = rs.getMetaData();
                int n = md.getColumnCount();
                StringBuilder cols = new StringBuilder("[");
                for (int i = 1; i <= n; i++) {
                    String label = md.getColumnLabel(i);
                    int dot = label == null ? -1 : label.lastIndexOf('.');    // Hive may prefix labels with the table alias
                    cols.append(i > 1 ? "," : "").append(str(dot >= 0 ? label.substring(dot + 1) : label));
                }
                StringBuilder rows = new StringBuilder("[");
                int count = 0;
                boolean truncated = false;
                while (rs.next()) {
                    if (count == limit) { truncated = true; break; }
                    rows.append(count++ > 0 ? "," : "").append('[');
                    for (int i = 1; i <= n; i++) rows.append(i > 1 ? "," : "").append(value(rs, i, md.getColumnType(i)));
                    rows.append(']');
                }
                return "\"columns\":" + cols + "],\"rows\":" + rows + "],\"truncated\":" + truncated;
            }
        }
    }

    /**
     * Tables of the session database with their column counts. Table names come from SHOW TABLES, a driver-side
     * command that keeps working when the Spark Thrift Server can no longer schedule jobs (JDBC getTables does not);
     * column counts from one getColumns call, else DESCRIBE TABLE per table within a time budget. A count that
     * could not be read is reported as null, never as 0.
     */
    private static String tables(Connection db, DatabaseMetaData meta, String schema, int limit) throws SQLException {
        Map<String, Integer> cols = new LinkedHashMap<>();
        try (Statement st = db.createStatement(); ResultSet rs = st.executeQuery("SHOW TABLES")) {
            ResultSetMetaData md = rs.getMetaData();
            int name = 1, temporary = 0;
            for (int i = 1; i <= md.getColumnCount(); i++) {
                String label = md.getColumnLabel(i).toLowerCase(Locale.ROOT);
                if (label.endsWith("tablename") || label.endsWith("tab_name")) name = i;
                if (label.endsWith("istemporary")) temporary = i;
            }
            while (rs.next() && cols.size() < limit) {
                if (temporary > 0 && rs.getBoolean(temporary)) continue;     // session temp views are not catalog tables
                cols.put(rs.getString(name), null);
            }
        } catch (SQLException showFailed) {
            try (ResultSet rs = meta.getTables(null, schema, "%", null)) {
                while (rs.next() && cols.size() < limit) cols.put(rs.getString("TABLE_NAME"), null);
            }
        }
        try (ResultSet rs = meta.getColumns(null, schema, "%", "%")) {
            Map<String, Integer> counted = new LinkedHashMap<>();
            while (rs.next()) counted.merge(rs.getString("TABLE_NAME"), 1, Integer::sum);
            for (String t : cols.keySet()) cols.put(t, counted.getOrDefault(t, 0));
        } catch (SQLException metadataFailed) {
            long deadline = System.currentTimeMillis() + 25_000;
            for (String t : cols.keySet()) {
                if (System.currentTimeMillis() > deadline) break;
                try (Statement st = db.createStatement();
                     ResultSet rs = st.executeQuery("DESCRIBE TABLE `" + t.replace("`", "``") + "`")) {
                    int n = 0;
                    while (rs.next()) {
                        String c = rs.getString(1);
                        if (c == null || c.trim().isEmpty() || c.startsWith("#")) break;   // partition / detail sections
                        n++;
                    }
                    cols.put(t, n);
                } catch (SQLException describeFailed) {
                    // leave the count unknown
                }
            }
        }
        StringBuilder out = new StringBuilder("[");
        for (Map.Entry<String, Integer> e : cols.entrySet()) {
            out.append(out.length() > 1 ? "," : "").append("{\"name\":").append(str(e.getKey()))
               .append(",\"cols\":").append(e.getValue() == null ? "null" : e.getValue().toString()).append('}');
        }
        return out.append(']').toString();
    }

    /**
     * Database named in the JDBC URL (jdbc:hive2://host:port/db;session?conf#vars), "default" when absent.
     * Connection.getSchema() is not used: the Hive driver implements it as SELECT current_database(), a query job.
     */
    static String database(String url) {
        String rest = url.replaceFirst("^jdbc:hive2://[^/]*", "");
        rest = rest.startsWith("/") ? rest.substring(1) : rest;
        int cut = rest.length();
        for (char c : new char[] {';', '?', '#'}) { int i = rest.indexOf(c); if (i >= 0 && i < cut) cut = i; }
        String db = rest.substring(0, cut).trim();
        return db.isEmpty() ? "default" : db;
    }

    private static String value(ResultSet rs, int i, int type) throws SQLException {
        switch (type) {
            case Types.TINYINT: case Types.SMALLINT: case Types.INTEGER: case Types.BIGINT: {
                long v = rs.getLong(i);
                return rs.wasNull() ? "null" : Long.toString(v);
            }
            case Types.FLOAT: case Types.REAL: case Types.DOUBLE: {
                double v = rs.getDouble(i);
                return rs.wasNull() || Double.isNaN(v) || Double.isInfinite(v) ? "null" : Double.toString(v);
            }
            case Types.DECIMAL: case Types.NUMERIC: {
                BigDecimal v = rs.getBigDecimal(i);
                return v == null ? "null" : v.toPlainString();
            }
            case Types.BOOLEAN: case Types.BIT: {
                boolean v = rs.getBoolean(i);
                return rs.wasNull() ? "null" : Boolean.toString(v);
            }
            default: {
                String v = rs.getString(i);
                return v == null ? "null" : str(v);
            }
        }
    }

    private static void fail(String stage, Throwable e) {
        Throwable root = e;
        StringBuilder msg = new StringBuilder();
        for (Throwable t = e; t != null && msg.length() < 600; t = t.getCause()) {
            root = t;
            if (t.getMessage() != null) msg.append(msg.length() > 0 ? " | " : "").append(t.getMessage());
        }
        String text = msg.length() > 600 ? msg.substring(0, 600) : msg.toString();
        System.out.println("{\"ok\":false,\"stage\":" + str(stage) + ",\"error_class\":" + str(root.getClass().getName())
                + ",\"message\":" + str(text) + "}");
    }

    private static String str(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20 || c == 0x2028 || c == 0x2029) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.append('"').toString();
    }
}
