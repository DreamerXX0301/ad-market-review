package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

const (
	installDirectory   = "AMR-Desktop"
	launcherName       = "AMR-Desktop-Preview.exe"
	manifestName       = "BUILD-MANIFEST.json"
	installationMarker = "AMR-INSTALLATION.json"
	maximumFileCount   = 12000
	maximumPayload     = 4 << 30
	maximumManifest    = 4 << 20
)

var installationCancelled = errors.New("用户取消安装")

type payloadManifest struct {
	Artifact       string            `json:"artifact"`
	Target         string            `json:"target"`
	DesktopVersion string            `json:"desktop_version"`
	WorkingTree    bool              `json:"working_tree"`
	Files          map[string]string `json:"files"`
}

type payloadFile struct {
	name string
	sha  string
	zip  *zip.File
}

type installationPayload struct {
	files []payloadFile
	bytes uint64
}

func verifySHA(value string) bool {
	if len(value) != 2*sha256.Size || strings.ToLower(value) != value {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func validWindowsComponent(name string) bool {
	if name == "" || name == "." || name == ".." || !utf8.ValidString(name) ||
		strings.HasSuffix(name, ".") || strings.HasSuffix(name, " ") ||
		strings.ContainsAny(name, "/\\:<>\"|?*") {
		return false
	}
	for _, c := range name {
		if c < 32 || c == 127 {
			return false
		}
	}
	base, _, _ := strings.Cut(strings.ToUpper(name), ".")
	base = strings.TrimRight(base, " ")
	switch base {
	case "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$":
		return false
	}
	for _, prefix := range []string{"COM", "LPT"} {
		if suffix, ok := strings.CutPrefix(base, prefix); ok && suffix != "" &&
			strings.Contains("0123456789¹²³", suffix) && utf8.RuneCountInString(suffix) == 1 {
			return false
		}
	}
	return true
}

func validVersion(version string) bool {
	parts := strings.Split(version, ".")
	if len(parts) < 2 || len(parts) > 3 {
		return false
	}
	for _, part := range parts {
		if part == "" || strings.Trim(part, "0123456789") != "" {
			return false
		}
	}
	return true
}

func readManifest(file *zip.File) (payloadManifest, error) {
	var manifest payloadManifest
	if file.UncompressedSize64 > maximumManifest {
		return manifest, errors.New("安装包文件清单超出限制")
	}
	stream, err := file.Open()
	if err != nil {
		return manifest, err
	}
	defer stream.Close()
	body, err := io.ReadAll(io.LimitReader(stream, maximumManifest+1))
	if err != nil {
		return manifest, err
	}
	if len(body) > maximumManifest || uint64(len(body)) != file.UncompressedSize64 {
		return manifest, errors.New("安装包文件清单大小不匹配")
	}
	if err := json.Unmarshal(body, &manifest); err != nil {
		return manifest, err
	}
	return manifest, nil
}

func inspectPayload(archive io.ReaderAt, size int64, checksum string) (installationPayload, error) {
	if !verifySHA(checksum) || size <= 0 || size > maximumPayload {
		return installationPayload{}, errors.New("安装程序缺少有效的内部校验值或载荷")
	}
	digest := sha256.New()
	if _, err := io.Copy(digest, io.NewSectionReader(archive, 0, size)); err != nil {
		return installationPayload{}, err
	}
	if hex.EncodeToString(digest.Sum(nil)) != checksum {
		return installationPayload{}, errors.New("安装包完整性校验失败，请重新取得安装文件")
	}
	zipped, err := zip.NewReader(archive, size)
	if err != nil {
		return installationPayload{}, err
	}
	if len(zipped.File) == 0 || len(zipped.File) > maximumFileCount {
		return installationPayload{}, errors.New("安装包文件数超出限制")
	}

	var result installationPayload
	var rootName string
	var manifestFile *zip.File
	// Check case-insensitive file and directory identities, including prefix
	// collisions, before allowing any write to the selected volume.
	directories := make(map[string]string)
	files := make(map[string]bool)
	for _, member := range zipped.File {
		parts := strings.Split(member.Name, "/")
		if len(parts) < 2 || member.NonUTF8 || !member.Mode().IsRegular() {
			return installationPayload{}, errors.New("安装包包含不支持的路径或文件类型")
		}
		for _, part := range parts {
			if !validWindowsComponent(part) {
				return installationPayload{}, errors.New("安装包包含不安全的文件名")
			}
		}
		if rootName == "" {
			rootName = parts[0]
		}
		if parts[0] != rootName {
			return installationPayload{}, errors.New("安装包包含多个程序目录")
		}
		for i := 2; i < len(parts); i++ {
			dir := strings.Join(parts[1:i], "/")
			key := strings.ToLower(dir)
			if existing, ok := directories[key]; files[key] || (ok && existing != dir) {
				return installationPayload{}, errors.New("安装包的目录名称冲突")
			}
			directories[key] = dir
		}
		name := strings.Join(parts[1:], "/")
		key := strings.ToLower(name)
		if files[key] || directories[key] != "" || key == strings.ToLower(installationMarker) {
			return installationPayload{}, errors.New("安装包的文件名称冲突")
		}
		files[key] = true
		if member.UncompressedSize64 > maximumPayload-result.bytes {
			return installationPayload{}, errors.New("安装包解压大小超出限制")
		}
		result.bytes += member.UncompressedSize64
		if name == manifestName {
			manifestFile = member
		}
		result.files = append(result.files, payloadFile{name: name, zip: member})
	}
	if manifestFile == nil {
		return installationPayload{}, errors.New("安装包缺少文件清单")
	}
	manifest, err := readManifest(manifestFile)
	if err != nil {
		return installationPayload{}, err
	}
	if manifest.Artifact != "private-windows-desktop-preview" || manifest.Target != "windows-x64" ||
		manifest.WorkingTree || !validVersion(manifest.DesktopVersion) ||
		rootName != "AMR-Desktop-"+manifest.DesktopVersion+"-Windows-x64" ||
		len(manifest.Files) != len(result.files)-1 {
		return installationPayload{}, errors.New("安装包目标或文件清单不匹配")
	}
	for _, required := range []string{launcherName, "runtime/python.exe", "verify.pem", "tray.ico", "使用说明.html"} {
		if _, present := manifest.Files[required]; !present {
			return installationPayload{}, fmt.Errorf("安装包缺少 %s", required)
		}
	}
	for i := range result.files {
		if result.files[i].name == manifestName {
			continue
		}
		result.files[i].sha = manifest.Files[result.files[i].name]
		if !verifySHA(result.files[i].sha) {
			return installationPayload{}, errors.New("安装包存在未列出或缺少校验值的文件")
		}
	}
	return result, nil
}

func extractFile(root *os.Root, file payloadFile) error {
	source, err := file.zip.Open()
	if err != nil {
		return err
	}
	defer source.Close()
	path := filepath.FromSlash(file.name)
	if parent := filepath.Dir(path); parent != "." {
		if err := root.MkdirAll(parent, 0700); err != nil {
			return err
		}
	}
	target, err := root.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	digest := sha256.New()
	count, readErr := io.Copy(io.MultiWriter(target, digest), io.LimitReader(source, int64(file.zip.UncompressedSize64)+1))
	closeErr := target.Close()
	if readErr != nil {
		return readErr
	}
	if closeErr != nil {
		return closeErr
	}
	if uint64(count) != file.zip.UncompressedSize64 ||
		(file.sha != "" && hex.EncodeToString(digest.Sum(nil)) != file.sha) {
		return fmt.Errorf("安装文件校验失败：%s", file.name)
	}
	return nil
}

func extractPayload(payload installationPayload, app string, progress func(int, int) bool) error {
	if err := os.Mkdir(app, 0700); err != nil {
		return err
	}
	root, err := os.OpenRoot(app)
	if err != nil {
		return err
	}
	defer root.Close()
	for i, file := range payload.files {
		if !progress(i, len(payload.files)) {
			return installationCancelled
		}
		if err := extractFile(root, file); err != nil {
			return err
		}
	}
	if !progress(len(payload.files), len(payload.files)) {
		return installationCancelled
	}
	marker, err := root.OpenFile(installationMarker, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	_, err = io.WriteString(marker, "{\"format\":1}\n")
	closeErr := marker.Close()
	return errors.Join(err, closeErr)
}
