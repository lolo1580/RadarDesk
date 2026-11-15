BEGIN { inblock = 0; depth = 0; buf = "" }
{
  if (!inblock && $0 ~ /us\.push\(new ol\.layer\.Tile\(\{/) {
    inblock = 1
    depth = 0
    buf = ""
  }
  if (inblock) {
    buf = buf $0 "\n"
    line = $0
    opens = gsub(/\{/, "{", line)
    line = $0
    closes = gsub(/\}/, "}", line)
    depth += opens - closes
    if (depth <= 0) {
      if (buf !~ /tiles\.arcgis\.com/) printf "%s", buf
      inblock = 0
      buf = ""
    }
    next
  }
  print
}
