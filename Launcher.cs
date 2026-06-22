using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

class Program
{
    static string RootDir;
    static string LogFile;
    static readonly object LogLock = new object();
    static string PythonExe;
    static string PythonwExe;
    static string LocalIP;
    static string UpstreamCQGIP;
    static string UpstreamHistIP;

    // ─── Entry point ────────────────────────────────────────────────────────
    static void Main()
    {
        RootDir = AppDomain.CurrentDomain.BaseDirectory;
        LogFile = Path.Combine(RootDir, "logs", "launcher_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".log");
        Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
        Log("=== Deepchart CQG Proxy Launcher ===");
        Log("Root: " + RootDir);

        if (!IsAdmin())
        {
            Log("Not admin — re-launching with elevation.");
            var psi = new ProcessStartInfo
            {
                FileName = Process.GetCurrentProcess().MainModule.FileName,
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
                CreateNoWindow = true
            };
            try { Process.Start(psi); Log("Elevated instance launched."); }
            catch (Exception ex) { Log("Elevation failed: " + ex.Message); }
            return;
        }
        Log("Running as administrator.");

        // Step 1: Resolve upstream IPs before hosts redirect
        ResolveUpstreamIPs();

        // Step 2: Find or download Python
        if (!FindPython())
        {
            Log("[FATAL] Python not found and auto-download failed. Please install Python 3.14 from python.org");
            return;
        }
        Log("Using Python: " + PythonExe);
        Log("Using Pythonw: " + PythonwExe);

        // Step 3: Install pip packages
        InstallPackages();

        // Step 4: Fix hosts file
        FixHosts();

        // Step 5: Apply profiles (templates, workspaces, indicator colors)
        ApplyProfiles();

        // Step 6: Apply roaming config (APPDATA\Deepchart) — only if not already configured
        ApplyRoamingConfig();

        // Step 7: Ensure patched_run exists (run patcher if needed)
        if (!EnsurePatchedRun())
        {
            Log("[FATAL] Could not create patched_run. Aborting.");
            return;
        }

        // Step 8: Kill old processes
        KillOld();

        // Step 9: Set env vars for child Python processes
        Environment.SetEnvironmentVariable("CQG_UPSTREAM_IP", UpstreamCQGIP, EnvironmentVariableTarget.Process);
        Environment.SetEnvironmentVariable("HIST_UPSTREAM_IP", UpstreamHistIP, EnvironmentVariableTarget.Process);

        // Step 10: Start historical server
        StartPython("vol_hist_server.py", "Historical Server");
        Thread.Sleep(3000);

        // Step 11: Start bridge proxy
        StartPython("bridge_mitm_proxy.py", "Bridge MITM");
        Thread.Sleep(4000);

        // Step 12: Launch VolumetricaBridge (hidden)
        LaunchBridge();

        // Step 13: Launch Deepchart (visible — the only window the user sees)
        LaunchDeepchart();

        Log("=== Launcher done ===");
    }

    // ─── Admin check ────────────────────────────────────────────────────────
    static bool IsAdmin()
    {
        try
        {
            var psi = new ProcessStartInfo("whoami.exe", "/groups")
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true
            };
            var proc = Process.Start(psi);
            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit(3000);
            return output.Contains("S-1-16-12288");
        }
        catch { return false; }
    }

    // ─── DNS resolution (bypasses hosts file via nslookup with public DNS) ──
    static string ResolveViaNslookup(string hostname, string dnsServer)
    {
        try
        {
            var psi = new ProcessStartInfo("nslookup", hostname + " " + dnsServer)
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true
            };
            var proc = Process.Start(psi);
            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit(5000);
            var matches = Regex.Matches(output, @"Address:\s+(\d+\.\d+\.\d+\.\d+)");
            string result = null;
            foreach (Match m in matches)
            {
                string ip = m.Groups[1].Value;
                if (ip != dnsServer) result = ip;
            }
            return result;
        }
        catch { return null; }
    }

    static void ResolveUpstreamIPs()
    {
        Log("Resolving upstream IPs via nslookup (bypassing hosts)...");
        UpstreamCQGIP = ResolveViaNslookup("demoapi.cqg.com", "8.8.8.8");
        if (string.IsNullOrEmpty(UpstreamCQGIP)) UpstreamCQGIP = "208.48.16.22";
        Log("  CQG: " + UpstreamCQGIP);

        UpstreamHistIP = ResolveViaNslookup("depth-it.historical.deepcharts.com", "8.8.8.8");
        if (string.IsNullOrEmpty(UpstreamHistIP)) UpstreamHistIP = UpstreamCQGIP;
        Log("  HIST: " + UpstreamHistIP);
    }

    // ─── Find or download Python ────────────────────────────────────────────
    static bool FindPython()
    {
        // Check portable Python first
        string portableW = Path.Combine(RootDir, "_python", "pythonw.exe");
        string portable = Path.Combine(RootDir, "_python", "python.exe");
        if (File.Exists(portableW))
        {
            PythonwExe = portableW;
            PythonExe = portable;
            if (!File.Exists(PythonExe)) PythonExe = PythonwExe;
            return true;
        }
        if (File.Exists(portable))
        {
            PythonExe = portable;
            PythonwExe = portable;
            return true;
        }

        // Search PATH for pythonw
        string[] candidates = { "pythonw.exe", "python3w.exe", "python.exe", "python3.exe" };
        foreach (var c in candidates)
        {
            try
            {
                var psi = new ProcessStartInfo(c, "--version")
                {
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    CreateNoWindow = true
                };
                var p = Process.Start(psi);
                string ver = p.StandardOutput.ReadToEnd();
                p.WaitForExit(2000);
                if (ver.Contains("Python 3"))
                {
                    if (c.Contains("pythonw"))
                        PythonwExe = c;
                    else if (PythonExe == null)
                        PythonExe = c;
                }
            }
            catch { }
        }

        // Search common install paths
        string[] roots = {
            @"C:\Python314", @"C:\Python313", @"C:\Python312", @"C:\Python311",
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) + @"\Python314",
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) + @"\Python313",
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) + @"\Python312",
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) + @"\Python311",
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) + @"\Programs\Python\Python314",
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) + @"\Programs\Python\Python313",
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) + @"\Programs\Python\Python312",
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) + @"\Programs\Python\Python311",
        };
        foreach (var d in roots)
        {
            string w = Path.Combine(d, "pythonw.exe");
            string e = Path.Combine(d, "python.exe");
            if (File.Exists(w) && PythonwExe == null) PythonwExe = w;
            if (File.Exists(e) && PythonExe == null) PythonExe = e;
        }

        // Fallback: pythonw = what we found
        if (PythonwExe == null) PythonwExe = PythonExe;
        if (PythonExe == null) PythonExe = PythonwExe;

        // If still not found, auto-download
        if (PythonExe == null) return DownloadPython();

        return true;
    }

    static bool DownloadPython()
    {
        Log("Python not found on system. Downloading portable Python 3.14...");
        string pyDir = Path.Combine(RootDir, "_python");
        Directory.CreateDirectory(pyDir);

        try
        {
            using (var wc = new WebClient())
            {
                string zipPath = Path.Combine(Path.GetTempPath(), "_py.zip");
                wc.Proxy = null;
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                wc.DownloadFile("https://www.python.org/ftp/python/3.14.6/python-3.14.6-embed-amd64.zip", zipPath);
                Log("Downloaded. Extracting...");

                var extractPsi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -Command \"Expand-Archive -Path '" + zipPath + "' -DestinationPath '" + pyDir + "' -Force\"")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                var extractProc = Process.Start(extractPsi);
                extractProc.WaitForExit(60000);
                File.Delete(zipPath);

                // Fix _pth file
                foreach (var f in Directory.GetFiles(pyDir, "python*._pth"))
                {
                    string content = File.ReadAllText(f);
                    content = content.Replace("#import site", "import site");
                    File.WriteAllText(f, content);
                }

                // Install pip
                string getPip = Path.Combine(Path.GetTempPath(), "_getpip.py");
                wc.DownloadFile("https://bootstrap.pypa.io/get-pip.py", getPip);
                Log("Installing pip...");

                var pipPsi = new ProcessStartInfo(Path.Combine(pyDir, "python.exe"), "\"" + getPip + "\" --quiet")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                var pipProc = Process.Start(pipPsi);
                pipProc.WaitForExit(60000);
                File.Delete(getPip);

                PythonExe = Path.Combine(pyDir, "python.exe");
                PythonwExe = Path.Combine(pyDir, "pythonw.exe");
                if (!File.Exists(PythonwExe)) PythonwExe = PythonExe;
                Log("Python installed at: " + pyDir);
                return true;
            }
        }
        catch (Exception ex)
        {
            Log("Auto-download failed: " + ex.Message);
            return false;
        }
    }

    // ─── Install pip packages ──────────────────────────────────────────────
    static void InstallPackages()
    {
        string req = Path.Combine(RootDir, "requirements.txt");
        if (!File.Exists(req))
        {
            Log("requirements.txt not found — skipping package install.");
            return;
        }
        Log("Installing pip packages...");
        try
        {
            var psi = new ProcessStartInfo(PythonExe, "-m pip install -r \"" + req + "\"")
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            var proc = Process.Start(psi);
            proc.WaitForExit(120000);
            Log("pip install completed.");
        }
        catch (Exception ex)
        {
            Log("pip install failed: " + ex.Message);
        }
    }

    // ─── Hosts file ─────────────────────────────────────────────────────────
    static string GetLocalIP()
    {
        try
        {
            var psi = new ProcessStartInfo("netsh", "interface ip show addresses")
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true
            };
            var proc = Process.Start(psi);
            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit(5000);
            var match = Regex.Match(output, @"IP Address:\s+(\d+\.\d+\.\d+\.\d+)");
            if (match.Success)
            {
                string ip = match.Groups[1].Value;
                if (ip != "127.0.0.1") return ip;
            }
        }
        catch { }

        // Fallback: ipconfig
        try
        {
            var psi = new ProcessStartInfo("ipconfig")
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true
            };
            var proc = Process.Start(psi);
            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit(5000);
            var match = Regex.Match(output, @"IPv4[^.]+[.:]\s+(\d+\.\d+\.\d+\.\d+)");
            if (match.Success) return match.Groups[1].Value;
        }
        catch { }

        return "127.0.0.1";
    }

    static void FixHosts()
    {
        Log("Updating hosts file...");
        try
        {
            LocalIP = GetLocalIP();
            string hostsPath = Path.Combine(Environment.SystemDirectory, "drivers", "etc", "hosts");
            string content = File.ReadAllText(hostsPath);
            string[] domains = {
                "real.deepcharts.com", "depth-it.deepcharts.com",
                "depth-it.historical.deepcharts.com", "data-b.historical.deepcharts.com"
            };

            // Remove existing entries
            foreach (var d in domains)
            {
                content = Regex.Replace(content,
                    @"^\d+\.\d+\.\d+\.\d+\s+" + Regex.Escape(d) + @"[ \t]*(\r?\n|$)",
                    "", RegexOptions.Multiline);
            }

            // Add new entries
            var sb = new StringBuilder(content.TrimEnd());
            sb.AppendLine();
            foreach (var d in domains)
                sb.AppendLine(LocalIP + "  " + d);

            File.WriteAllText(hostsPath, sb.ToString());
            Log("Hosts file updated (" + LocalIP + ").");
        }
        catch (Exception ex)
        {
            Log("Failed to update hosts: " + ex.Message);
        }
    }

    // ─── Apply profiles ─────────────────────────────────────────────────────
    static void CopyDirectory(string source, string dest)
    {
        Directory.CreateDirectory(dest);
        foreach (string file in Directory.GetFiles(source))
        {
            string target = Path.Combine(dest, Path.GetFileName(file));
            File.Copy(file, target, true);
        }
        foreach (string dir in Directory.GetDirectories(source))
        {
            string target = Path.Combine(dest, Path.GetFileName(dir));
            CopyDirectory(dir, target);
        }
    }

    static void ApplyProfiles()
    {
        string profilesDir = Path.Combine(RootDir, "profiles");
        string dataDir = Path.Combine(RootDir, "patched_run", "data");

        if (!Directory.Exists(profilesDir))
        {
            Log("No profiles/ directory — skipping.");
            return;
        }

        Directory.CreateDirectory(dataDir);
        Log("Applying profiles...");
        foreach (string dir in Directory.GetDirectories(profilesDir))
        {
            string name = Path.GetFileName(dir);
            if (name.Equals("Roaming", StringComparison.OrdinalIgnoreCase)) continue; // handled separately
            string dest = Path.Combine(dataDir, name);
            CopyDirectory(dir, dest);
            Log("  Profile: " + name);
        }
        foreach (string file in Directory.GetFiles(profilesDir))
        {
            string dest = Path.Combine(dataDir, Path.GetFileName(file));
            File.Copy(file, dest, true);
        }
        Log("Profiles applied.");
    }

    static void ApplyRoamingConfig()
    {
        string roamingSrc = Path.Combine(RootDir, "profiles", "Roaming");
        string roamingDst = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Deepchart");

        if (!Directory.Exists(roamingSrc))
        {
            Log("No profiles/Roaming/ — skipping roaming config.");
            return;
        }

        // Only apply if config.settings doesn't already exist (first run)
        string existingConfig = Path.Combine(roamingDst, "config.settings");
        if (File.Exists(existingConfig))
        {
            Log("Roaming config already exists — skipping (preserving credentials).");
            return;
        }

        Log("Applying roaming config...");
        Directory.CreateDirectory(roamingDst);
        foreach (string file in Directory.GetFiles(roamingSrc))
        {
            string target = Path.Combine(roamingDst, Path.GetFileName(file));
            File.Copy(file, target, true);
            Log("  Roaming: " + Path.GetFileName(file));
        }
    }

    // ─── Ensure patched_run ─────────────────────────────────────────────────
    static bool EnsurePatchedRun()
    {
        string patchedExe = Path.Combine(RootDir, "patched_run", "Deepchart.exe");
        if (File.Exists(patchedExe))
        {
            Log("patched_run/ exists.");
            return true;
        }

        // Try to run patcher
        Log("patched_run/ not found — running patcher...");
        string patcher = Path.Combine(RootDir, "patcher.ps1");
        if (File.Exists(patcher))
        {
            try
            {
                var psi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + patcher + "\" -NoPause")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                var proc = Process.Start(psi);
                proc.WaitForExit(120000);
                if (File.Exists(patchedExe))
                {
                    Log("patcher.ps1 completed successfully.");
                    return true;
                }
            }
            catch (Exception ex)
            {
                Log("patcher.ps1 failed: " + ex.Message);
            }
        }

        Log("Could not create patched_run/.");
        return false;
    }

    // ─── Process management ─────────────────────────────────────────────────
    static void KillOld()
    {
        Log("Killing old processes...");
        foreach (string name in new[] { "python", "pythonw", "python3", "python3w" })
        {
            foreach (var proc in Process.GetProcessesByName(name))
            {
                try { proc.Kill(); Log("  Killed " + name + " PID " + proc.Id); } catch { }
            }
        }
        foreach (string name in new[] { "Deepchart", "VolumetricaBridge" })
        {
            foreach (var proc in Process.GetProcessesByName(name))
            {
                try { proc.Kill(); Log("  Killed " + name + " PID " + proc.Id); } catch { }
            }
        }
        Thread.Sleep(2000);
    }

    static void RunHidden(string file, string args, string workDir, string label)
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = file,
                Arguments = args,
                WorkingDirectory = workDir ?? RootDir,
                WindowStyle = ProcessWindowStyle.Hidden,
                CreateNoWindow = true,
                UseShellExecute = true
            };
            var proc = Process.Start(psi);
            Log(label + " started (PID " + proc.Id + ")");
        }
        catch (Exception ex)
        {
            Log(label + " FAILED: " + ex.Message);
        }
    }

    static void StartPython(string scriptName, string label)
    {
        string path = Path.Combine(RootDir, scriptName);
        if (!File.Exists(path))
        {
            Log(label + " script not found: " + path);
            return;
        }
        if (!File.Exists(PythonwExe))
        {
            Log(label + " Pythonw not found at " + PythonwExe);
            // Try python.exe instead
            if (File.Exists(PythonExe))
                RunHidden(PythonExe, "\"" + path + "\"", RootDir, label + " (via python)");
            return;
        }
        RunHidden(PythonwExe, "\"" + path + "\"", RootDir, label);
    }

    static void LaunchBridge()
    {
        string bridgeExe = Path.Combine(RootDir, "patched_run", "bridge", "VolumetricaBridge.exe");
        if (File.Exists(bridgeExe))
        {
            RunHidden(bridgeExe, "", Path.GetDirectoryName(bridgeExe), "VolumetricaBridge");
        }
        else
        {
            Log("VolumetricaBridge.exe not found at " + bridgeExe);
        }
        Thread.Sleep(6000);
    }

    static void LaunchDeepchart()
    {
        string newRoot = Path.Combine(RootDir, "patched_run");

        // Unblock files
        Log("Unblocking files...");
        RunHidden("powershell.exe",
            "-NoProfile -Command \"Get-ChildItem '" + newRoot + "' -Recurse -File | Unblock-File -ErrorAction SilentlyContinue\"",
            null, "Unblock-File");

        // Launch the REAL Deepchart (saved as Deepchart.Original.exe by patcher)
        string appExe = Path.Combine(newRoot, "Deepchart.Original.exe");
        if (!File.Exists(appExe))
        {
            Log("Deepchart.Original.exe not found at " + appExe);
            return;
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = appExe,
                WorkingDirectory = Path.GetDirectoryName(appExe),
                UseShellExecute = true
            };
            var proc = Process.Start(psi);
            Log("Deepchart launched (PID " + proc.Id + ").");
        }
        catch (Exception ex)
        {
            Log("Deepchart launch FAILED: " + ex.Message);
        }
    }

    // ─── Logging ───────────────────────────────────────────────────────────
    static void Log(string msg)
    {
        lock (LogLock)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + msg;
            File.AppendAllText(LogFile, line + Environment.NewLine);
            Console.WriteLine(msg);
        }
    }
}
