export const ROLE = {admin:'复核主管', analyst:'分析员', viewer:'只读查看'};
export const STATUS = {queued:'排队中', running:'正在计算', complete:'计算完成', failed:'运行失败'};
export const REVIEW = {pending:'待复核', accepted:'已通过', returned:'已退回'};
export const ARCHIVE = {building:'归档去重中', verifying:'复原核对中', releasing:'释放原读数中', archived:'已归档',
  restoring:'解档复原中', reclaiming:'回收读数中', retired:'已复原', failed:'归档失败'};
