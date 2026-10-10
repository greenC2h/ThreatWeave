# Jev 简单案例

`run_priority_test.py` 会从仓库根目录的 `.env` 自动读取
`JEVMODEL_API_KEY`，然后让 Jev 判断一个“所有客户无法登录、业务中断”问题的
紧急程度。

运行：

```powershell
.\.venv\Scripts\python.exe .\jev_minimal_test\run_priority_test.py
```

这个案例使用 `score` 类型，评分标准依次为：低、中、高、严重。脚本不会打印或
写出 API key。
