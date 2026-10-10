# 团伙背景

摩诃草，又名 Patchwork、白象、Hangover、Dropping Elephant 等，奇安信内部跟踪编号 APT-Q-36。该组织被普遍认为具有南亚地区背景，其最早攻击活动可追溯到 2009 年 11 月，已持续活跃 10 余年。该组织主要针对亚洲地区的国家进行网络间谍活动，攻击目标包括政府、军事、电力、工业、科研教育、外交和经济等领域的组织机构。

## 事件概述

奇安信威胁情报中心近期发现一种与摩诃草组织有关的木马，该木马与远程服务器通信采用 WebSocket 和 HTTP 协议相结合的方式，建立 WebSocket 连接的服务器接口带有"stream"字符串，木马获取指令与回传操作结果均在该 WebSocket 通道中进行，而用 HTTP 完成一些诸如文件传输之类的操作。我们还发现这种木马与摩诃草使用的 Spyder 下载器有一些相似性，根据以上特点我们将该木马命名为 StreamSpy。

## 详细分析

相关样本信息如下：

| MD5 | 文件名 | 说明 |
| --- | --- | --- |
| 1c335be51fc637b50d41533f3bef2251 | OPS-VII-SIR.zip | 包含 StreamSpy 木马的 zip 文件 |
| f78fd7e4d92743ef6026de98291e8dee | Annexure.exe | StreamSpy 木马，版本 1.0.0.1 |
| e0ac399cff3069104623cc38395bd946 | List of officials nominated for rewards 2025-2026.zip | 包含 StreamSpy 木马的 zip 文件 |
| c3c277cca23f3753721435da80cad1ea | List of officials nominated for rewards 2025-2026.exe | StreamSpy 木马，版本 1.0.0.2 |
| e4a7a85feff6364772cf1d12d8153a69 | - | StreamSpy 木马，版本 1.0.0.2 |

目前发现的 1.0.0.2 版本的 StreamSpy 木马相比 1.0.0.1 版本变化不大，下面将以 1.0.0.1 版本 StreamSpy 木马为例展开分析。

压缩包 OPS-VII-SIR.zip 的下载链接为"hxxps://firebasescloudemail.com/reports/OPS-VII-SIR.zip"，该压缩包中有两个 PDF 文件和一个用 PDF 图标伪装的 EXE 文件，后者就是 StreamSpy 木马本身，诱使受害者不加分辨地运行恶意程序。

### 初始化

木马首先从资源区解密出配置数据。

解密出的配置数据为 JSON 字符串，有 network、identity 和 persistence 三部分内容，network 中出现 C2 服务器 www.mydropboxbackup[.]com:443。

木马配置数据还包括设置与 C2 服务器交互使用的 URL 路径，如下 URL 路径会拼接上解密配置中 network 的 prefix 字段（这里是"analytics"），形成最终的路径。值得注意的是—些 URL 路径并没有在与 C2 服务器的通信过程中使用，这可能意味着该木马仍在开发过程中。

如果未能成功从资源区获取配置信息，则对配置数据进行如下设置。

### 信息收集

木马收集各类信息，包括主机名、用户名、操作系统版本、杀软信息，并通过 WMI 查询 UUID、主板序列号和处理器 ID，进而生成感染设备对应的标识。

收集的设备信息和配置数据中的 identity 信息组合起来，后续将发往 C2 服务器的"/[prefix]/auth"接口。

### 持久化

如果配置中 persistence 的 enable_app_path 字段为 true，或者木马的文件路径不是指定路径时，执行持久化操作。

木马持久化方式有三种，由配置数据中 persistence 的 startup_method 字段的数值决定，为 1 时创建计划任务。

为 2 时设置注册表的 RunOnce 键值。

为 3 时，在 Starup 目录创建 LNK 文件。

### C&C 通信

木马与 C2 服务器通信采用结合 WebSocket 和 HTTP 协议的方式。

| 木马操作 | 网络协议 | 相关接口 |
| --- | --- | --- |
| 与服务器建立通信连接 | HTTP | /[prefix]/auth |
| 向服务器发送心跳包 | HTTP | /[prefix]/status |
| 获取木马指令与回传结果 | WebSocket | /[prefix]/stream |
| 文件上传与下载 | HTTP | 多个接口 |

木马首先尝试与 C2 服务器建立连接，向 C2 服务器的"/[prefix]/auth"接口发送前面收集的信息，如果服务器返回指定响应内容，则说明连接建立成功。

连接建立之后，木马一方面定期向 C2 服务器的"/[prefix]/status"接口发送心跳包，数据包中的 device_token 为信息收集阶段生成的设备标识。

另一方面同 C2 服务器的"/[prefix]/stream"接口建立 WebSocket 连接。

通过发送 getCommand 信息获取木马指令，木马指令执行完毕发送 ack_command 信息。

木马指令分发函数为 sub_413EB0，该函数还可以根据传入参数决定开启或关闭获取木马指令的开关。

木马指令与对应功能详细说明如下。

(1) 信息 type 为 terminal_input

将命令通过管道传入创建的 shell 进程，实现远程命令执行。

(2) "F1A5C3": 从指定 URL 下载文件到指定路径，然后调用 ShellExecuteExW 打开文件。

(3) "B8C1D2": 将执行命令的 shell 设置为 cmd。

(4) "E4F5A6": 将执行命令的 shell 设置为 powershell。

(5) "FL_SH1": 关闭所有 shell。

(6) "C9E3D4", "E7F8A9", "H1K4R8", "C0V3RT"

这 4 个指令都是从 C2 服务器下载加密 zip 压缩包，解压后调用 ShellExecuteExW 打开其中的文件。这些指令的参数有 4 个，分别是"zip_name", "zip_password", "location"和"folder"。

这些指令下载的 URL 路径有所不同。

| 木马指令 | 下载文件的 URL 路径 |
| --- | --- |
| C9E3D4 | /[prefix]/update/[zip_name] |
| E7F8A9 | /[prefix]/package/[zip_name] |
| H1K4R8 | /[prefix]/assets/[zip_name] |
| C0V3RT | /sniperagent/cache/[zip_name] |

(7) "F2B3C4": 获取设备连接的所有磁盘信息，包括文件系统、是否可移除、总容量、空闲容量等。

(8) "D5E6F7

该指令实现文件上传和下载操作，包含 "file_path", "session_id" 和 "resume_offset" 三个指令参数。文件上传到服务器使用 C2 服务器的 "/[prefix]/sync" 接口，从服务下载文件使用 "/[prefix]/fetch" 接口。

(9) "A8B9C0"

该指令同样具备文件上传功能，包含 "file_path", "session_id" 和 "file_size" 三个指令参数，也使用 "/[prefix]/sync" 接口。

(10) "D1E2F3": 删除文件。

(11) "A4B5C6": 重命名文件。

(12) "D7E8F9": 枚举指定目录。

### 新版本 StreamSpy

目前发现的 1.0.0.2 版本的 StreamSpy 木马样本配置信息如下，C2 均为"www.virtualworldsapinner[.]com:443"，prefix 分别为"metrics"和"insights"。

相比 1.0.0.1 版本，1.0.0.2 版本的 StreamSpy 木马整体变化不大，不同之处在于 URL 路径配置中增加了"cache"接口。

木马指令"C0V3RT"下载后续载荷的路径由"/sniperagent/cache/[zip_name]"，变为"/[prefix]/cache/[zip_name]"。

## 溯源关联

通过上述 StreamSpy 木马我们关联到其他恶意软件，包括摩诃草组织 Spyder 下载器变种和肚脑虫（Donot）组织的样本，这些样本在早些时候已被安全研究人员公开披露过[1, 2]。

### Spyder 变种

StreamSpy 木马的下载站点 firebasescloudemail[.]com 关联到一些 Spyder 变种，相关样本信息如下：

| MD5 | 文件名 | 数字签名信息 |
| --- | --- | --- |
| 0fe90212062957a529cba3938613c4da | vpn.exe | "Fidus Software Consulting Inc." |
| df626ce2ad3d3dea415984a9d3839373 | JuD NEW MARKAZ DETAILS.exe | "Fidus Software Consulting Inc." |

这些样本同样从资源区 HTML 类型数据中获取配置信息，解密方式与 StreamSpy 类似，跳过第 1 字节，然后进行 AES 解密，并且解密密钥和 StreamSpy 木马相同。

解密的配置数据中除了 C2 信息"adobefileshare.com/getData"，还有一个带有"spyder"字符串的路径。

收集设备信息，包括根据磁盘 volume 信息生成的受害者标识、主机名、用户名、操作系统版本、杀软信息，并拼接上面解密得到的配置数据中的一些信息。

将收集信息通过 POST 请求发送到"hxxp://adobefileshare.com/getData"，服务器返回响应中会包含一个数字字符串。

(1) 如果数字为除 5 和 6 之外的非 0 值

首先向"hxxp://adobefileshare.com/getfilename"发送 POST 请求，请求的正文数据为受害者标识 UID。该请求用于获取待下载的加密 zip 压缩包保存在本地的文件名和解压密码，两者在响应内容中通过"!"进行分隔。再向"hxxp://adobefileshare.com/download"发送请求下载文件，然后执行解压操作，调用 ShellExecuteA 打开解压出的文件。这种文件下载方式和以往的 Spyder 下载器基本一致[3]。

(2) 如果数字为 5，通过 powershell 或 cmd 执行命令。

(3) 如果数字为 6，会进行木马指令分发。

涉及的木马指令如下。

| 指令 | 说明 |
| --- | --- |
| listDrives | 获取所有磁盘信息 |
| listDirectory | 枚举目录信息 |
| deleteFile | 删除文件 |
| renameFile | 重命名文件 |
| executeFile | 调用 CreateProcessW 执行文件 |
| downloadFile | 下载文件 |
| uploadFile | 上传文件 |

如果将恶意软件这三个分支的功能结合起来，可以发现与 StreamSpy 木马所具有的功能基本一致。

近期发现另一个同类型 Spyder 变种，该样本只保留了后续载荷下载功能，去掉了 shell 命令执行和木马指令分发的两个分支，C2 为"azureinternalupdates.com"。

| MD5 | 文件名 | 数字签名信息 |
| --- | --- | --- |
| 838e4d85346001dd04e11359b04c7c24 | Terrorist Financing through Digital Currency, Cryptocurrency 2025.exe | "Fidus Software Consulting Inc." |

### 肚脑虫样本

StreamSpy 木马（MD5: f78fd7e4d92743ef6026de98291e8dee）带有的数字签名可以关联到另一个样本 20c9ac59c444625a7ee364b410da8f11。该恶意软件会收集设备信息，包括 CPU 型号、操作系统产品名称和 build number、用户名、主机名、安装的软件列表等。

收集信息加密后拼接到"mopd="之后向"hxxps://brityservice.info/ZxStpliGBsfdutMawer/sIOklbgrTYULKcsdGBZxsfetmw"发送。

恶意软件根据 C2 服务器的响应决定是否下载后续载荷，下载后续的 URL 为 "hxxps://brityservice.info/ZxStpliGBsfdutMawer/lkhgBrPUyXbgIlErAStyilzsh/N1/SA"。

该样本与肚脑虫组织以往攻击活动使用的样本[4]高度相似，而肚脑虫与摩诃草组织的攻击样本共用数字签名的情况之前也出现过[5]。

## 总结

摩诃草组织 StreamSpy 木马和 Spyder 变种的出现表明该组织在不断迭代自己的攻击武器库。在 StreamSpy 木马中，攻击者试图用 WebSocket 信道进行木马指令下发和结果回传，以避开对 HTTP 报文的检测和审查。另外关联到的样本再次印证了摩诃草和肚脑虫两个攻击团队在资源共享方面有一些联系。

## 防护建议

奇安信威胁情报中心提醒广大用户，谨防钓鱼攻击，切勿打开社交媒体分享的来历不明的链接，不点击执行未知来源的邮件附件，不运行标题夸张的未知文件，不安装非正规途径来源的 APP。做到及时备份重要文件，更新安装补丁。

若需运行，安装来历不明的应用，可先通过奇安信威胁情报文件深度分析平台（https://sandbox.ti.qianxin.com/sandbox/page）进行判别。目前已支持包括 Windows、安卓平台在内的多种格式文件深度分析。

目前，基于奇安信威胁情报中心的威胁情报数据的全线产品，包括奇安信威胁情报平台（TIP）、天擎、天眼高级威胁检测系统、奇安信 NGSOC、奇安信态势感知等，都已经支持对此类攻击的精确检测。

## IOC

### MD5

（摩诃草）

1c335be51fc637b50d41533f3bef2251

e0ac399cff3069104623cc38395bd946

f78fd7e4d92743ef6026de98291e8dee （StreamSpy 木马）

c3c277cca23f3753721435da80cad1ea （StreamSpy 木马）

e4a7a85feff6364772cf1d12d8153a69 （StreamSpy 木马）

0fe90212062957a529cba3938613c4da （Spyder 变种）

df626ce2ad3d3dea415984a9d3839373 （Spyder 变种）

838e4d85346001dd04e11359b04c7c24 （Spyder 变种）

（肚脑虫）

20c9ac59c444625a7ee364b410da8f11

### C&C

（摩诃草）

firebasescloudemail.com

mydropboxbackup.com

virtualworldsapinner.com

adobefileshare.com

azureinternalupdates.com

（肚脑虫）

scrollzshare.info

brityservice.info

### URL

（摩诃草）

wss://www.mydropboxbackup[.]com/analytics/stream

wss://www.virtualworldsapinner[.]com/metrics/stream

wss://www.virtualworldsapinner[.]com/insights/stream

hxxps://www.mydropboxbackup.com/analytics/

hxxps://www.virtualworldsapinner.com/metrics/

hxxps://www.virtualworldsapinner.com/insights/

hxxp://adobefileshare.com/getData

hxxp://adobefileshare.com/getfilename

hxxp://adobefileshare.com/download

hxxp://azureinternalupdates.com/getData

hxxp://azureinternalupdates.com/getfilename

hxxp://azureinternalupdates.com/download

（肚脑虫）

hxxps://scrollzshare.info/eeCetyUo8Tr

hxxps://brityservice.info/ZxStpliGBsfdutMawer/sIOklbgrTYULKcsdGBZxsfetmw

hxxps://brityservice.info/ZxStpliGBsfdutMawer/lkhgBrPUyXbgIlErAStyilzsh/N1/SA

## 参考链接

[1]. https://x.com/malwrhunterteam/status/1985321347279626438

[2]. https://x.com/malwrhunterteam/status/1986334542123159792

[3]. https://ti.qianxin.com/blog/articles/analysis-of-new-variants-and-components-of-patchwork-spyder-downloader-cn/

[4]. https://ti.qianxin.com/blog/articles/analysis-of-the-attack-activity-of-the-apt-q-38-using-pdf-document-decoys-cn/

[5]. https://ti.qianxin.com/blog/articles/patchwork-attack-weapons-reuse-the-infrastructure-of-the-donot-cn/

APT 南亚地区 PATCHWORK DONOT
