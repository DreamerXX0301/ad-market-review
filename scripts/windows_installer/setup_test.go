package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fixtureRoot = "AMR-Desktop-0.1.26-Windows-x64/"

func fixtureZip(t *testing.T, invalidName string, wrongFileSHA bool) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	manifest := payloadManifest{
		Artifact: "private-windows-desktop-preview", Target: "windows-x64",
		DesktopVersion: "0.1.26", Files: make(map[string]string),
	}
	for _, name := range []string{launcherName, "runtime/python.exe", "verify.pem", "tray.ico", "使用说明.html"} {
		entry, err := writer.Create(fixtureRoot + name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := io.WriteString(entry, name); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256([]byte(name))
		manifest.Files[name] = hex.EncodeToString(digest[:])
	}
	if invalidName != "" {
		entry, err := writer.Create(invalidName)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := io.WriteString(entry, "extra"); err != nil {
			t.Fatal(err)
		}
	}
	if wrongFileSHA {
		manifest.Files[launcherName] = strings.Repeat("0", 64)
	}
	entry, err := writer.Create(fixtureRoot + manifestName)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.NewEncoder(entry).Encode(manifest); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func inspectFixture(t *testing.T, fixture []byte) (installationPayload, error) {
	t.Helper()
	sum := sha256.Sum256(fixture)
	return inspectPayload(bytes.NewReader(fixture), int64(len(fixture)), hex.EncodeToString(sum[:]))
}

func TestVerifiedInstallWritesMarkerLast(t *testing.T) {
	fixture := fixtureZip(t, "", false)
	payload, err := inspectFixture(t, fixture)
	if err != nil {
		t.Fatal(err)
	}
	app := filepath.Join(t.TempDir(), "app")
	if err := extractPayload(payload, app, func(done, total int) bool {
		if _, err := os.Stat(filepath.Join(app, installationMarker)); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("marker appeared during copy at %d/%d: %v", done, total, err)
		}
		return true
	}); err != nil {
		t.Fatal(err)
	}
	marker, err := os.ReadFile(filepath.Join(app, installationMarker))
	if err != nil || string(marker) != "{\"format\":1}\n" {
		t.Fatalf("unexpected install marker %q: %v", marker, err)
	}
	python, err := os.ReadFile(filepath.Join(app, "runtime", "python.exe"))
	if err != nil || string(python) != "runtime/python.exe" {
		t.Fatalf("unexpected runtime file %q: %v", python, err)
	}
	if err := extractPayload(payload, app, func(int, int) bool { return true }); !errors.Is(err, os.ErrExist) {
		t.Fatalf("existing app directory was not protected: %v", err)
	}
}

func TestInspectRejectsUnsafeZipMembersBeforeWrites(t *testing.T) {
	for _, name := range []string{
		fixtureRoot + "../escaped.txt",
		fixtureRoot + "runtime\\python.exe",
		fixtureRoot + "runtime/CON.dat",
		fixtureRoot + "runtime/COM².log",
		fixtureRoot + "runtime/file.exe:stream",
		fixtureRoot + "runtime/file.exe.",
		fixtureRoot + "Runtime/other.exe",
		fixtureRoot + "RUNTIME",
		fixtureRoot + strings.ToUpper(launcherName),
		fixtureRoot + strings.ToLower(installationMarker),
		"second-root/unexpected",
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := inspectFixture(t, fixtureZip(t, name, false)); err == nil {
				t.Fatal("unsafe archive was accepted")
			}
		})
	}
}

func TestArchiveAndFileSHAFailuresAndCancellation(t *testing.T) {
	fixture := fixtureZip(t, "", true)
	if _, err := inspectPayload(bytes.NewReader(fixture), int64(len(fixture)), strings.Repeat("0", 64)); err == nil {
		t.Fatal("incorrect embedded SHA-256 was accepted")
	}
	payload, err := inspectFixture(t, fixture)
	if err != nil {
		t.Fatal(err)
	}
	app := filepath.Join(t.TempDir(), "app")
	if err := extractPayload(payload, app, func(int, int) bool { return true }); err == nil {
		t.Fatal("incorrect file SHA-256 was accepted")
	}
	if _, err := os.Stat(filepath.Join(app, installationMarker)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("failed extraction wrote marker: %v", err)
	}
	app = filepath.Join(t.TempDir(), "app")
	if err := extractPayload(payload, app, func(int, int) bool { return false }); err != installationCancelled {
		t.Fatalf("cancellation was not preserved: %v", err)
	}
	if _, err := os.Stat(filepath.Join(app, installationMarker)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("cancelled extraction wrote marker: %v", err)
	}
}

func TestReleaseArchiveRoundTrip(t *testing.T) {
	path := os.Getenv("AMR_SETUP_FIXTURE_ARCHIVE")
	if path == "" {
		t.Skip("set AMR_SETUP_FIXTURE_ARCHIVE to verify a built package")
	}
	archive, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	info, err := archive.Stat()
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.New()
	if _, err := io.Copy(digest, archive); err != nil {
		t.Fatal(err)
	}
	payload, err := inspectPayload(archive, info.Size(), hex.EncodeToString(digest.Sum(nil)))
	if err != nil {
		t.Fatal(err)
	}
	app := filepath.Join(t.TempDir(), "app")
	if err := extractPayload(payload, app, func(int, int) bool { return true }); err != nil {
		t.Fatal(err)
	}
	t.Logf("validated and extracted %d files, %d bytes", len(payload.files), payload.bytes)
}
