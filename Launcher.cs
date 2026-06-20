using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;

class Program
{
    static string ScriptsDir;
    static string LogFile;
    static readonly object LogLock = new object();
    static string PythonExe;

    static void Main()
    {
        ScriptsDir = AppDomain.CurrentDomain.BaseDirectory;
        LogFile = Path.Combine(ScriptsDir, "logs", "launcher_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".log");
        Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
        Log("Launcher starting from " + ScriptsDir);

        if (!IsAdmin())
        {
            Log("Not admin — re-launching with elevation.");
            var psi = new ProcessStartInfo
            {
                FileName = Process.GetCurrentProcess().MainModule.FileName,
                UseShellExecute = true,
                Verb = "runas"
            };
            try { Process.Start(psi); Log("Elevated instance launched."); }
            catch (Exception ex) { Log("Elevation failed: " + ex.Message); }
            return;
        }

        Log("Running as administrator.");
        PythonExe = FindPython();
        if (string.IsNullOrEmpty(PythonExe))
        {
            Log("Python not found. Aborting.");
            return;
        }
        Log("Using Python: " + PythonExe);

        KillOld();
        StartPython("vol_hist_server.py", "Volumetrica Historical Server");
        StartPython("bridge_mitm_proxy.py", "Bridge MITM Proxy");
        Thread.Sleep(4000);
        LaunchDeepchart();
        Log("Launcher done.");
    }

    static string FindPython()
    {
        string[] candidates = { "python.exe", "python3.exe" };
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
                if (ver.Contains("Python 3")) return c;
            }
            catch { }
        }
        string[] dirs = {
            @"C:\Python314", @"C:\Python313", @"C:\Python312", @"C:\Python311",
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) + @"\Programs\Python"
        };
        foreach (var d in dirs)
        {
            foreach (var f in new[] { "python.exe", "python3.exe" })
            {
                string path = Path.Combine(d, f);
                if (File.Exists(path)) return path;
            }
        }
        return null;
    }

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
            var psi = new ProcessStartInfo
            {
                FileName = file,
                Arguments = args,
                WorkingDirectory = workDir ?? ScriptsDir,
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
        string newRoot = Path.Combine(ScriptsDir, "patched_run");
        string bridgeExe = Path.Combine(newRoot, "bridge", "VolumetricaBridge.exe");
        string appExe = Path.Combine(newRoot, "Deepchart.exe");

        Log("Unblocking files...");
        RunHidden("powershell.exe",
            "-NoProfile -Command \"Get-ChildItem '" + newRoot + "' -Recurse -File | Unblock-File -ErrorAction SilentlyContinue\"",
            null, "Unblock-File");

        if (File.Exists(bridgeExe))
        {
            RunHidden(bridgeExe, "", Path.GetDirectoryName(bridgeExe), "VolumetricaBridge");
        }
        else
        {
            Log("VolumetricaBridge.exe not found at " + bridgeExe);
        }

        Thread.Sleep(6000);

        if (File.Exists(appExe))
        {
            RunHidden(appExe, "", Path.GetDirectoryName(appExe), "Deepchart");
            Log("Deepchart launched.");
        }
        else
        {
            Log("Deepchart.exe not found at " + appExe);
        }
    }
}
