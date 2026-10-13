using System.Reflection;
using System.Runtime.Loader;
using System.Collections;

string binaries=args[0], output=args[1];
// This helper only configures the disposable plugin host. It never runs an
// engine build or replaces rules on disk; replacement is local to this process.
if (!args.Contains("-SkipBuild") || !args.Contains("-NoEngineChanges"))
    throw new ArgumentException("Only isolated configuration is allowed");
AssemblyLoadContext.Default.ResolvingUnmanagedDll += (_, name) => {
    string path=Path.Combine(binaries,"runtimes/win-x64/native",name+".dll");
    return File.Exists(path)?System.Runtime.InteropServices.NativeLibrary.Load(path):IntPtr.Zero;
};
AssemblyLoadContext.Default.Resolving += (_, name) => {
    string path=Path.Combine(binaries,name.Name+".dll");
    return File.Exists(path)?AssemblyLoadContext.Default.LoadFromAssemblyPath(path):null;
};
try {
    var ubt=Assembly.LoadFrom(Path.Combine(binaries,"UnrealBuildTool.dll"));
    var core=Assembly.LoadFrom(Path.Combine(binaries,"EpicGames.Core.dll"));
    var logger=core.GetType("EpicGames.Core.Log")!.GetProperty("Logger")!.GetValue(null);
    var rules=ubt.GetType("UnrealBuildTool.RulesCompiler")!.GetMethod("CreateEngineRulesAssembly")!.Invoke(null,new object?[]{true,true,false,logger})!;
    var type=rules.GetType();
    var fileType=core.GetType("EpicGames.Core.FileReference")!;
    Directory.CreateDirectory(output);
    var references=new List<string>();
    void Rebuild(object graph){
        if(type.GetProperty("Parent")!.GetValue(graph) is object parent)Rebuild(parent);
        var field=type.GetField("CompiledAssembly",BindingFlags.Instance|BindingFlags.NonPublic)!;
        if(field.GetValue(graph) is not Assembly current)return;
        var sources=type.GetProperty("AssemblySourceFiles")!.GetValue(graph)!;
        var defines=type.GetProperty("PreprocessorDefines")!.GetValue(graph)!;
        var target=Activator.CreateInstance(fileType,Path.GetFullPath(Path.Combine(output,"VFX"+current.GetName().Name+".dll")));
        var rebuilt=(Assembly?)ubt.GetType("UnrealBuildTool.DynamicCompilation")!.GetMethod("CompileAndLoadAssembly")!.Invoke(null,new object?[]{target,sources,logger,references.ToArray(),defines,false,true,false});
        if(rebuilt is null)throw new Exception("Rules compilation failed");
        field.SetValue(graph,rebuilt);references.Add(rebuilt.Location);
        Console.WriteLine("Rebuilt rules in isolated directory: "+rebuilt.Location);
    }
    Rebuild(rules);
    return (int)ubt.EntryPoint!.Invoke(null,new object?[]{args.Skip(2).ToArray()})!;
} catch(Exception e){Console.Error.WriteLine(e.ToString());return 1;}
