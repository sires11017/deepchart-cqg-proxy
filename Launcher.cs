using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

class Program
{
    static string ScriptsDir;
    static string LogFile;
    static readonly object LogLock = new object();
    static readonly string PythonExe = @"C:\Python314\python.exe";
    static readonly string WhoamiExe = @"C:\Windows\System32\whoami.exe";
    static readonly string PowershellExe = @"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe";

    static void Main()
    {
        // Find scripts directory
        string userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string oneDriveDir = Path.Combine(userProfile, "OneDrive", "Documents", "1.Deepcharts");
        ScriptsDir = Directory.Exists(oneDriveDir) ? oneDriveDir : AppDomain.CurrentDomain.BaseDirectory;

        LogFile = Path.Combine(ScriptsDir, "logs", "launcher_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".log");
        Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
        Log("Launcher v2 starting from " + ScriptsDir);

        // Elevate to admin if not already
        if (!IsAdmin())
        {
            Log("Not admin — re-launching with elevation.");
            var psi = new ProcessStartInfo
            {
                FileName = Process.GetCurrentProcess().MainModule.FileName,
                UseShellExecute = true,
                Verb = "runas"
            };
            try
            {
                Process.Start(psi);
                Log("Elevated instance launched. Exiting.");
            }
            catch (Exception ex)
            {
                Log("Elevation failed: " + ex.Message);
            }
            return;
        }

        Log("Running as administrator.");

        // Kill old processes
        KillOld();

        // Start Python servers
        StartPython("vol_hist_server.py", "Volumetrica Historical Server");
        StartPython("bridge_mitm_proxy.py", "Bridge MITM Proxy");

        // Wait for bridge to bind
        Thread.Sleep(4000);

        // Launch Deepchart stack
        LaunchDeepchart();

        Log("Launcher done. All services started.");
    }

    static bool IsAdmin()
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = WhoamiExe,
                Arguments = "/groups",
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

    static void Log(string msg)
    {
        lock (LogLock)
        {
            File.AppendAllText(LogFile,
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + msg + Environment.NewLine);
        }
    }

    static void RunHidden(string file, string args, string workDir, string label)
    {
        try
        {
            bool isPython = file.EndsWith("python.exe", StringComparison.OrdinalIgnoreCase);
            var psi = new ProcessStartInfo
            {
                FileName = file,
                Arguments = args,
                WorkingDirectory = workDir ?? ScriptsDir,
                WindowStyle = isPython ? ProcessWindowStyle.Normal : ProcessWindowStyle.Hidden,
                CreateNoWindow = !isPython,
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

    static void KillOld()
    {
        Log("Killing old processes...");
        foreach (var proc in Process.GetProcessesByName("python"))
        {
            try { proc.Kill(); Log("  Killed python PID " + proc.Id); } catch { }
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

    static void StartPython(string scriptName, string label)
    {
        string path = Path.Combine(ScriptsDir, scriptName);
        if (!File.Exists(path))
        {
            Log(label + " script not found: " + path);
            return;
        }
        if (!File.Exists(PythonExe))
        {
            Log(label + " Python not found at " + PythonExe);
            return;
        }
        RunHidden(PythonExe, "\"" + path + "\"", ScriptsDir, label);
    }

    static void LaunchDeepchart()
    {
        string newRoot = @"C:\Deepchart\patched_run";
        string bridgeExe = Path.Combine(newRoot, "bridge", "VolumetricaBridge.exe");
        string appExe = Path.Combine(newRoot, "Deepchart.exe");

        // Unblock files
        Log("Unblocking files...");
        RunHidden(PowershellExe,
            "-NoProfile -Command \"Get-ChildItem '" + newRoot + "' -Recurse -File | Unblock-File -ErrorAction SilentlyContinue\"",
            null, "Unblock-File");

        // Start VolumetricaBridge
        if (File.Exists(bridgeExe))
        {
            RunHidden(bridgeExe, "", Path.GetDirectoryName(bridgeExe), "VolumetricaBridge");
        }
        else
        {
            Log("VolumetricaBridge.exe not found at " + bridgeExe);
            string fallback = Path.Combine(ScriptsDir, "patched_run", "bridge", "VolumetricaBridge.exe");
            if (File.Exists(fallback))
                RunHidden(fallback, "", Path.GetDirectoryName(fallback), "VolumetricaBridge (fallback)");
        }

        Thread.Sleep(6000);

        // Start Deepchart
        if (File.Exists(appExe))
        {
            RunHidden(appExe, "", Path.GetDirectoryName(appExe), "Deepchart");
            Log("Deepchart launched successfully.");
        }
        else
        {
            Log("Deepchart.exe not found at " + appExe);
            string fallback = Path.Combine(ScriptsDir, "patched_run", "Deepchart.exe");
            if (File.Exists(fallback))
                RunHidden(fallback, "", Path.GetDirectoryName(fallback), "Deepchart (fallback)");
        }
    }

}
