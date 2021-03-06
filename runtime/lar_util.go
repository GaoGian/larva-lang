import (
    "reflect"
    "math"
    "runtime"
    "path/filepath"
    "strings"
)

func lar_util_fmod_float(a, b float32) float32 {
    return float32(math.Mod(float64(a), float64(b)))
}

func lar_util_fmod_double(a, b float64) float64 {
    return math.Mod(a, b)
}

type lar_util_go_tb struct {
    file string
    line int
}

type lar_util_lar_tb struct {
    file     string
    line     int
    fom_name string
}

func lar_util_convert_go_tb_to_lar_tb(file string, line int, func_name string) (string, int, string, bool) {
    lar_tb, ok := lar_util_tb_map[lar_util_go_tb{file: file, line: line}]
    if !ok {
        //没找到对应的，抹掉函数名后返回
        return file, line, "", true
    }
    if lar_tb == nil {
        return "", 0, "", false
    }
    return lar_tb.file, lar_tb.line, lar_tb.fom_name, true
}

var lar_util_GOROOT_path string

func init() {
    //通过回溯调用栈找到reflect库目录，继而解析GOROOT路径，不能用runtime.GOROOT()是因为需要编译时的GOROOT而非运行时的
    reflect.ValueOf(func () {
        sep := string(filepath.Separator)
        reflect_path_suffix := sep + "src" + sep + "reflect"
        for i := 0; true; i ++ {
            _, file, _, ok := runtime.Caller(i)
            if !ok {
                panic("获取编译信息失败：无法获取GOROOT，调用栈中找不到reflect包")
            }
            file_dir := filepath.Dir(file)
            if strings.HasSuffix(file_dir, reflect_path_suffix) {
                lar_util_GOROOT_path = file_dir[: len(file_dir) - len(reflect_path_suffix) + 1] //末尾需要保留一个sep
                if lar_util_GOROOT_path == sep {
                    panic("获取编译信息失败：GOROOT为空")
                }
                break
            }
        }
    }).Call(nil)
}
