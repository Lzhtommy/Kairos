# 部署说明

架构：推送到 `main` 分支 → GitHub Actions 构建 → rsync 到阿里云 ECS → Nginx 托管静态文件。

## 服务器一次性初始化（以 Ubuntu/Debian 为例，root 登录后执行）

```bash
# 1. 安装 nginx 和 rsync
apt update && apt install -y nginx rsync

# 2. 创建专用部署用户（不给 sudo，只用于接收文件）
useradd -m -s /bin/bash deploy
mkdir -p /var/www/kairos
chown -R deploy:deploy /var/www/kairos

# 3. 为 deploy 用户配置 SSH 公钥（公钥来自下面「生成密钥对」一步）
mkdir -p /home/deploy/.ssh
echo "<粘贴 kairos-deploy.pub 的内容>" >> /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh

# 4. 配置 nginx（配置文件在本目录 nginx-kairos.conf）
cp nginx-kairos.conf /etc/nginx/conf.d/kairos.conf
# Ubuntu 默认站点会抢 80 端口，禁用它
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

Alibaba Cloud Linux / CentOS 把 `apt` 换成 `yum`，其余相同（没有 sites-enabled，跳过那一步）。

## 阿里云安全组

在 ECS 控制台的安全组入方向放行 80 端口（HTTP）。上 HTTPS 后再放行 443。

## 生成部署密钥对（本地执行）

```bash
ssh-keygen -t ed25519 -f kairos-deploy -N "" -C "kairos-deploy"
```

- `kairos-deploy.pub`（公钥）→ 追加到服务器 `/home/deploy/.ssh/authorized_keys`
- `kairos-deploy`（私钥）→ 存入 GitHub 仓库 Secrets

## GitHub 仓库 Secrets

仓库 Settings → Secrets and variables → Actions → New repository secret，添加三个：

| Secret 名 | 值 |
|---|---|
| `SERVER_HOST` | ECS 公网 IP |
| `SERVER_USER` | `deploy` |
| `SSH_PRIVATE_KEY` | `kairos-deploy` 私钥文件的完整内容 |

配置完成后，推送到 main 即自动部署；也可以在 Actions 页面手动触发（workflow_dispatch）。

## 验证

浏览器访问 `http://<ECS公网IP>`。直接 IP 访问无需 ICP 备案；绑定域名并解析到大陆服务器则需要先完成备案。
