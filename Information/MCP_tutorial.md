# MCP 使用说明

MCP 是 PFS 的可选扩展协议，用于把经过授权的外部工具接入 Agent。它默认关闭；只有在用户明确配置并启动服务器后，PFS 才会尝试连接。

## 配置前准备

1. 确认 MCP 服务的来源、权限、网络范围和数据处理方式。
2. 为 MCP 服务准备独立目录，不要把依赖和配置散落到用户目录。
3. 不要把 API Key、Cookie、密码或带有个人数据的配置提交到 Git。

## 本地 MCP 配置

在 PFS 的 MCP 面板中填写服务的启动命令、参数和工作目录。一个通用的本地配置形态如下：

```json
{
  "command": "node",
  "args": ["/path/to/mcp-server.js"],
  "cwd": "/path/to/mcp"
}
```

Windows 路径请使用实际目录，例如：

```json
{
  "command": "node",
  "args": ["C:\\pfs-mcp\\server.js"],
  "cwd": "C:\\pfs-mcp"
}
```

配置完成后，先点击连接，再检查工具列表、服务状态和错误信息。没有真实连接响应时，只能视为配置完成，不能视为 MCP 能力已经验收。

## Playwright MCP 示例

以下命令只用于创建一个独立的本地实验目录：

```powershell
mkdir C:\pfs-mcp
cd C:\pfs-mcp
npm init -y
npm install @playwright/test @playwright/mcp
npx playwright install
```

安装完成后，根据本机包版本确认 MCP 的实际启动入口，再将命令、参数和工作目录填入 PFS。不要直接复制未经确认的绝对路径。

## 远程 MCP

远程 MCP 需要服务方提供明确的 URL、认证方式、证书要求和权限范围。连接前先用目标环境提供的健康检查验证；不要把远程地址、令牌或响应内容写入公开日志。

## 安全边界

- MCP 返回内容按不可信输入处理；
- 任何写入、发送消息、上传或删除操作都应先确认目标和范围；
- 服务器异常、权限不足或配置不完整时，PFS 应安全失败，不自动切换到未知服务；
- 本地 MCP 配置存在不等于真实外部服务已经可用。
