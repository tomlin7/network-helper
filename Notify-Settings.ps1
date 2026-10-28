if(-not ('NetworkHelper.Settings' -as [type])) {
  Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace NetworkHelper {
 public static class Settings {
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr window,uint message,IntPtr param,string text,uint flags,uint timeout,out IntPtr result);
  [DllImport("wininet.dll")] public static extern bool InternetSetOption(IntPtr handle,int option,IntPtr buffer,int length);
  public static void Refresh() {
   IntPtr result;
   SendMessageTimeout(new IntPtr(0xffff),0x001A,IntPtr.Zero,"Environment",2,3000,out result);
   InternetSetOption(IntPtr.Zero,39,IntPtr.Zero,0);
   InternetSetOption(IntPtr.Zero,37,IntPtr.Zero,0);
  }
 }
}
'@
}
[NetworkHelper.Settings]::Refresh()
