#!/usr/bin/env bash
# RSS 自动拉取脚本 - 用于 cron 定时任务
#
# 使用方法:
#   1. 添加到 crontab: crontab -e
#   2. 每天早上 9 点拉取: 0 9 * * * /path/to/kg-pull-rss.sh
#   3. 每 6 小时拉取: 0 */6 * * * /path/to/kg-pull-rss.sh

set -euo pipefail

# 配置
VAULT_DIR="/Users/bt1q/Github-Projects/knowledge-graph"
LOG_DIR="$VAULT_DIR/.kg/logs"
LOG_FILE="$LOG_DIR/rss-pull-$(date +%Y%m%d).log"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 记录开始时间
echo "========================================" >> "$LOG_FILE"
echo "RSS Pull Started: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# 切换到 vault 目录
cd "$VAULT_DIR"

# 激活虚拟环境（如果使用）
# source venv/bin/activate

# 执行拉取
kg pull rss --since 7 >> "$LOG_FILE" 2>&1

# 记录结束时间
echo "========================================" >> "$LOG_FILE"
echo "RSS Pull Completed: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# 可选: 同步索引
# kg sync >> "$LOG_FILE" 2>&1

# 清理 30 天前的日志
find "$LOG_DIR" -name "rss-pull-*.log" -mtime +30 -delete

exit 0
