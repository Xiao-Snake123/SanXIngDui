# GitHub 代码上传完全指南

## 📚 目录
- [准备工作](#准备工作)
- [第一步：创建 GitHub 账户](#第一步创建-github-账户)
- [第二步：安装 Git](#第二步安装-git)
- [第三步：配置 Git](#第三步配置-git)
- [第四步：创建仓库](#第四步创建仓库)
- [第五步：初始化本地项目](#第五步初始化本地项目)
- [第六步：连接本地和远程仓库](#第六步连接本地和远程仓库)
- [第七步：上传代码](#第七步上传代码)
- [常见问题解决](#常见问题解决)
- [日常使用流程](#日常使用流程)

---

## 准备工作

你需要准备好：
- 一台电脑（Windows、Mac 或 Linux）
- 网络连接
- 一个代码编辑器（VS Code 推荐）
- 大约 15 分钟时间

---

## 第一步：创建 GitHub 账户

### 1.1 访问 GitHub 官网
- 打开浏览器，访问：https://github.com
- 点击右上角的 **Sign up** 按钮

### 1.2 填写注册信息
- **Email**：输入你的邮箱地址
- **Password**：输入强密码（建议大小写字母+数字+符号混合）
- **Username**：输入用户名（这是你在 GitHub 上的唯一标识，例如：Xiao-Snake123）

### 1.3 完成验证
- 完成邮箱验证（GitHub 会发送验证邮件）
- 点击邮件中的链接完成账户激活

### 1.4 设置账户信息
- 进入 Settings（设置）
- 完成基本信息配置
- 上传头像（可选）

---

## 第二步：安装 Git

Git 是一个版本控制工具，用来管理代码版本。

### Windows 系统
1. 访问：https://git-scm.com/download/win
2. 下载最新版本
3. 双击安装文件，一路点击 **Next** 即可
4. 安装完成后，重启电脑

### Mac 系统
```bash
# 方法1：使用 Homebrew（推荐）
brew install git

# 方法2：访问 https://git-scm.com/download/mac
```

### Linux 系统
```bash
# Ubuntu/Debian
sudo apt-get install git

# CentOS/RedHat
sudo yum install git
```

### 验证安装
打开命令行（Windows 用户打开 Git Bash 或 PowerShell，Mac/Linux 用户打开 Terminal）：
```bash
git --version
```

如果看到版本号，说明安装成功。

---

## 第三步：配置 Git

配置你的用户名和邮箱，这样提交代码时会记录你的身份。

打开命令行，执行：
```bash
# 设置全局用户名
git config --global user.name "你的用户名"

# 设置全局邮箱
git config --global user.email "你的邮箱"
```

例如：
```bash
git config --global user.name "Xiao-Snake123"
git config --global user.email "xiao@example.com"
```

### 验证配置
```bash
git config --global --list
```

你应该看到：
```
user.name=你的用户名
user.email=你的邮箱
```

---

## 第四步：创建仓库

仓库（Repository）就是存放你代码的地方。

### 4.1 在 GitHub 网页上创建
1. 登录 GitHub
2. 点击右上角的 **+** 号，选择 **New repository**
3. 填写信息：
   - **Repository name**：输入仓库名称（例如：my-project）
   - **Description**：输入描述（可选，例如：这是我的第一个项目）
   - **Public/Private**：选择 Public（公开）或 Private（私密）
     - Public：任何人都能看到你的代码
     - Private：只有你授权的人能看到
   - **Initialize this repository with**：
     - ✅ Add a README file（建议勾选，添加项目说明文件）
     - ✅ Add .gitignore（建议勾选，用来排除不必要的文件）
     - ✅ Choose a license（可选，选择开源协议）

4. 点击 **Create repository** 按钮

### 4.2 获取仓库链接
创建完成后，你会看到一个绿色的 **Code** 按钮，点击它，复制链接（选择 HTTPS）：
```
https://github.com/你的用户名/仓库名.git
```

---

## 第五步：初始化本地项目

### 5.1 方案 A：全新项目

如果你还没有本地代码，先从 GitHub 克隆仓库：

```bash
# 进入你想要放项目的文件夹
cd ~/Documents

# 克隆仓库（会创建一个新文件夹）
git clone https://github.com/你的用户名/仓库名.git

# 进入项目文件夹
cd 仓库名
```

现在你可以在这个文件夹里创建或编辑代码了。

### 5.2 方案 B：已有本地代码

如果你已经有一个本地代码文件夹，需要关联到 GitHub：

```bash
# 进入你的项目文件夹
cd /path/to/your/project

# 初始化 Git 仓库
git init

# 查看文件状态
git status
```

---

## 第六步：连接本地和远程仓库

这一步是关键，将你的本地代码和 GitHub 上的仓库连接起来。

```bash
# 添加远程仓库地址（将 URL 替换为你的）
git remote add origin https://github.com/你的用户名/仓库名.git

# 验证连接（应该看到 origin 的 fetch 和 push 链接）
git remote -v
```

---

## 第七步：上传代码

这是最后一步，将你的代码上传到 GitHub。

### 7.1 查看文件状态
```bash
git status
```

你会看到哪些文件是新增的、修改的或删除的。

### 7.2 添加要提交的文件

**方式 1：添加所有文件**
```bash
git add .
```

**方式 2：添加特定文件**
```bash
git add 文件名1 文件名2
```

**例如：**
```bash
# 添加所有 .js 文件
git add *.js

# 添加整个 src 文件夹
git add src/
```

### 7.3 提交代码

提交是为这个版本添加说明，方便以后查看。

```bash
git commit -m "提交说明"
```

**提交说明的写法：**
- ✅ 好的例子：`"第一版：完成登录功能"`
- ✅ 好的例子：`"修复bug：用户注册失败"`
- ✅ 好的例子：`"更新：添加用户头像功能"`

例如：
```bash
git commit -m "初始化项目：添加基础框架和依赖"
```

### 7.4 推送到 GitHub

```bash
# 第一次推送，需要指定分支
git push -u origin main

# 以后推送只需要：
git push
```

如果你的默认分支是 `master` 而不是 `main`，改为：
```bash
git push -u origin master
```

### 7.5 验证上传成功

- 打开浏览器
- 访问 https://github.com/你的用户名/仓库名
- 你应该能看到你上传的代码文件了！

---

## 常见问题解决

### 问题 1：提示 "Permission denied (publickey)"

**原因**：Git 无法访问你的 GitHub 账户

**解决方案**：
```bash
# 方式 1：使用 HTTPS（需要输入密码）
git remote set-url origin https://github.com/你的用户名/仓库名.git

# 然后推送时会要求输入用户名和密码
git push
```

### 问题 2：提示 "failed to push some refs"

**原因**：本地代码和远程仓库不同步

**解决方案**：
```bash
# 先拉取远程最新代码
git pull origin main

# 然后再推送
git push origin main
```

### 问题 3：提示 "Please tell me who you are"

**原因**：没有配置 Git 用户信息

**解决方案**：
```bash
# 重新配置用户名和邮箱
git config --global user.name "你的用户名"
git config --global user.email "你的邮箱"

# 然后重新提交
git commit -m "你的提交说明"
```

### 问题 4：不小心提交了不想要的文件

**解决方案**：

```bash
# 方式 1：撤销最后一次提交
git reset --soft HEAD~1

# 然后删除不需要的文件
git reset 文件名

# 重新提交
git commit -m "新的提交说明"
```

### 问题 5：想要上传大文件但提示超过大小限制

**原因**：GitHub 单个文件大小限制为 100MB

**解决方案**：
```bash
# 使用 Git LFS（大文件存储）
git lfs install
git lfs track "*.zip"  # 追踪 zip 文件
git add .gitattributes
git push
```

---

## 日常使用流程

学会了基础操作后，日常工作就是这样的循环：

### 循环流程（TLDR 版本）

```bash
# 1. 查看文件状态
git status

# 2. 添加所有修改
git add .

# 3. 提交更改
git commit -m "做了什么修改"

# 4. 推送到 GitHub
git push
```

### 如果有团队合作

```bash
# 1. 先拉取最新代码（防止冲突）
git pull

# 2. 进行代码编辑...

# 3. 然后按上面的循环操作
git add .
git commit -m "说明"
git push
```

---

## 💡 最佳实践

### ✅ DO（应该做）

- ✅ 经常提交代码（不要一次改很多东西再提交）
- ✅ 写清晰的提交说明（让别人知道你改了什么）
- ✅ 在推送前拉取最新代码（避免冲突）
- ✅ 使用分支（创建不同分支做不同功能）
- ✅ 给重要版本打标签（方便回溯）

### ❌ DON'T（不应该做）

- ❌ 不要提交无用文件（使用 .gitignore）
- ❌ 不要提交敏感信息（如 API 密钥、密码）
- ❌ 不要直接在 main 分支上开发（创建新分支）
- ❌ 不要使用无意义的提交说明（如 "修改了代码"）

---

## 🎓 下一步学习

掌握了基础后，可以学习更高级的用法：

- **分支管理**：`git branch` - 创建和管理分支
- **代码合并**：`git merge` - 合并两个分支的代码
- **查看历史**：`git log` - 查看提交历史
- **撤销操作**：`git revert` - 撤销某次提交
- **标签管理**：`git tag` - 给版本打标签

可以在 [这里](https://git-scm.com/doc) 找到详细文档。

---

## 📞 需要帮助？

- GitHub 官方帮助中心：https://docs.github.com
- Git 官方文档：https://git-scm.com/doc
- Stack Overflow：https://stackoverflow.com/questions/tagged/github

---

## 🎉 恭喜！

现在你已经掌握了将代码上传到 GitHub 的全部知识！

开始你的第一个项目吧！💪
