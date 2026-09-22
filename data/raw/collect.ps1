$base = "c:\Users\Administrator\Desktop\SXD\SanXIngDui\data\raw"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

$items = @(
  # Sichuan Provincial Cultural Relics and Archaeology Research Institute - public briefs (official)
  @{url="https://www.sckg.com/uploads/soft/20240514/2-240514103U5564.pdf"; folder="official"; name="sckg_k4_pit_excavation_brief_2024.pdf"; license="Sichuan Inst. of Archaeology - K4 pit excavation brief (institute public)"},
  @{url="https://www.sckg.com/uploads/soft/20240429/2-2404291A101941.pdf"; folder="official"; name="sckg_2015_yueliangwan_excavation.pdf"; license="Sichuan Inst. of Archaeology - 2015 Yueliangwan excavation (institute public)"},
  @{url="https://www.sckg.com/uploads/soft/20230509/2-230509155415R6.pdf"; folder="official"; name="sckg_guanghan_chronicle.pdf"; license="Guanghan Sanxingdui chronicle (institute public)"},

  # Open-access scholarly literature (academic)
  @{url="https://www.cambridge.org/core/services/aop-cambridge-core/content/view/D96494368471CF7CBA817690CDCA5A75/S0003598X22001508a.pdf/div-class-title-new-discoveries-at-the-sanxingdui-bronze-age-site-in-south-west-china-div.pdf"; folder="academic"; name="antiquity_2022_new_discoveries.pdf"; license="Antiquity 2022 - open access paper"},
  @{url="https://www.nature.com/articles/s40494-024-01531-8.pdf"; folder="academic"; name="heritage_science_2024_ivory.pdf"; license="Heritage Science 2024 - open access paper"},
  @{url="https://www.isccac.org/d/file/articles/2024-07-02/25fdea110c393daa1f147c90cfa53f1e.pdf"; folder="academic"; name="isccac_2024_sanxingdui_civilization.pdf"; license="ISCCAC 2024 conference paper (site public)"},
  @{url="https://pdfs.semanticscholar.org/fc0c/8b425e6cae84941b0cc6df3c676dd4369804.pdf"; folder="academic"; name="semanticscholar_2024_microbial_diversity.pdf"; license="Semantic Scholar open PDF"},

  # Official media / news report PDFs (museum)
  @{url="https://paper.people.com.cn/rmrbhwb/images/2021-06/14/12/rmrbhwb2021061412.pdf"; folder="museum"; name="people_daily_2021_0614_archaeology.pdf"; license="People's Daily Overseas 2021-06-14 report"},
  @{url="https://whwb.cjn.cn/resfile/2025-09-29/15/B2025-09-29A1501.pdf"; folder="museum"; name="cjn_2025_0929_forum_results.pdf"; license="Changjiang Daily 2025-09-29 forum results report"},
  @{url="https://paper.studytimes.cn/images/2024-04/05/A6/20240405A6_pdf.pdf"; folder="museum"; name="study_times_2024_0405.pdf"; license="Study Times 2024-04-05 report"},
  @{url="http://epaper.tyrbw.com/tywb/resfile/2023-11-17/20/tywb2023111720.pdf"; folder="museum"; name="tyrbw_2023_1117_multidisciplinary.pdf"; license="Taiyuan Daily 2023-11-17 multidisciplinary results report"},

  # Official website page archives (museum/html)
  @{url="https://www.sxd.cn/"; folder="museum"; name="sxd_cn_home.html"; license="Sanxingdui Museum official homepage (public web page)"},
  @{url="https://www.museumschina.cn/museums/details?id=51068121800003"; folder="museum"; name="museumschina_sanxingdui.html"; license="MuseumsChina Sanxingdui museum detail (public web page)"}
)

$log = @()
foreach ($it in $items) {
  $dir = Join-Path $base $it.folder
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $dest = Join-Path $dir $it.name
  try {
    Invoke-WebRequest -Uri $it.url -OutFile $dest -UserAgent $ua -TimeoutSec 60 -ErrorAction Stop
    $sz = (Get-Item $dest).Length
    $st = if ($sz -gt 0) { "OK" } else { "EMPTY" }
    $log += [PSCustomObject]@{name=$it.name; folder=$it.folder; url=$it.url; license=$it.license; bytes=$sz; status=$st; fetched=(Get-Date -Format "yyyy-MM-dd")}
  } catch {
    $log += [PSCustomObject]@{name=$it.name; folder=$it.folder; url=$it.url; license=$it.license; bytes=0; status="FAIL:$($_.Exception.Message)"; fetched=(Get-Date -Format "yyyy-MM-dd")}
  }
}
$log | Export-Csv -Path (Join-Path $base "manifest.csv") -NoTypeInformation
$log | Format-Table name, folder, bytes, status | Out-String | Write-Host
