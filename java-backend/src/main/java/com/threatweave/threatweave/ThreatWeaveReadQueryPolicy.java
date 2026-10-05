package com.threatweave.threatweave;

import com.threatweave.common.exception.BusinessException;
import java.util.Locale;
import java.util.Set;
import net.sf.jsqlparser.parser.CCJSqlParserUtil;
import net.sf.jsqlparser.statement.Statement;
import net.sf.jsqlparser.statement.Statements;
import net.sf.jsqlparser.statement.select.Select;
import net.sf.jsqlparser.util.TablesNamesFinder;
import org.springframework.stereotype.Component;

/** 验证模型提交的只读 SQL，只允许 ThreatWeave 业务数据。 */
@Component
public class ThreatWeaveReadQueryPolicy {
    public static final int MAX_ROWS = 200;
    private static final int MAX_JOINS = 4;
    private static final int MAX_SELECT_DEPTH = 3;
    private static final Set<String> ALLOWED_TABLES = Set.of(
            "threatweave.documents", "threatweave.entities", "threatweave.entity_aliases",
            "threatweave.relations", "threatweave.provenance", "workflow.document_processing");
    private static final Set<String> BLOCKED_FUNCTIONS = Set.of(
            "pg_sleep", "dblink", "lo_import", "lo_export", "set_config", "current_setting");

    /** 解析单条 SELECT 并验证表范围与复杂度，返回可安全包装执行的 SQL。 */
    public String validate(String sql) {
        if (sql == null || sql.isBlank()) {
            throw new BusinessException("只读查询 SQL 不能为空");
        }
        try {
            Statements statements = CCJSqlParserUtil.parseStatements(sql);
            if (statements.getStatements().size() != 1 || !(statements.getStatements().get(0) instanceof Select select)) {
                throw new BusinessException("只允许单条 SELECT 或 WITH SELECT 查询");
            }
            String normalized = select.toString().toLowerCase(Locale.ROOT);
            if (normalized.contains("for update") || normalized.contains("for share") || normalized.contains("recursive")) {
                throw new BusinessException("只读查询不允许锁定或递归语句");
            }
            if (count(normalized, " join ") > MAX_JOINS || count(normalized, "select ") > MAX_SELECT_DEPTH) {
                throw new BusinessException("查询 JOIN 数或嵌套深度超过限制");
            }
            for (String function : BLOCKED_FUNCTIONS) {
                if (normalized.contains(function + "(")) {
                    throw new BusinessException("查询使用了不允许的函数");
                }
            }
            for (String table : new TablesNamesFinder().getTableList(statements.getStatements().get(0))) {
                if (!ALLOWED_TABLES.contains(table.toLowerCase(Locale.ROOT))) {
                    throw new BusinessException("查询只能访问 ThreatWeave 业务数据");
                }
            }
            return select.toString();
        } catch (BusinessException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new BusinessException("只读查询 SQL 无效或不受支持");
        }
    }

    private static int count(String value, String needle) {
        int count = 0;
        int offset = 0;
        while ((offset = value.indexOf(needle, offset)) >= 0) {
            count++;
            offset += needle.length();
        }
        return count;
    }
}
