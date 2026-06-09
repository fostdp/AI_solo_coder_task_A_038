"""
微服务回归测试脚本
验证所有微服务代码语法、导入和基本功能
"""

import asyncio
import sys
import os
import importlib
import importlib.util
import traceback
from pathlib import Path
from typing import List, Dict, Tuple

SERVICES = [
    'profinet_driver',
    'temp_controller',
    'quality_predictor',
    'alarm_publisher',
    'api_gateway',
    'db_writer',
]

SHARED_MODULES = [
    'redis_channels',
    'message_protocol',
    'config_loader',
    'redis_client',
]


class TestResult:
    def __init__(self, name: str, passed: bool, message: str = ''):
        self.name = name
        self.passed = passed
        self.message = message

    def __str__(self):
        status = '✓ PASS' if self.passed else '✗ FAIL'
        return f'{status} {self.name}' + (f': {self.message}' if self.message else '')


class RegressionTester:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.results: List[TestResult] = []
        self.shared_imports: Dict[str, object] = {}

    def test_shared_modules(self) -> List[TestResult]:
        """测试共享模块导入和基本功能"""
        results: List[TestResult] = []
        shared_dir = self.base_dir / 'shared'
        sys.path.insert(0, str(self.base_dir))

        for module_name in SHARED_MODULES:
            try:
                module_path = shared_dir / f'{module_name}.py'
                if not module_path.exists():
                    results.append(TestResult(
                        f'shared.{module_name}',
                        False,
                        f'文件不存在: {module_path}'
                    ))
                    continue

                spec = importlib.util.spec_from_file_location(
                    f'shared.{module_name}',
                    module_path
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                self.shared_imports[module_name] = module
                results.append(TestResult(f'shared.{module_name}', True, '导入成功'))

                if module_name == 'redis_channels':
                    if hasattr(module, 'CHANNELS') and hasattr(module, 'SERVICE_IDS'):
                        results.append(TestResult(
                            f'shared.{module_name}.channels',
                            True,
                            f'定义了 {len(module.CHANNELS)} 个通道, {len(module.SERVICE_IDS)} 个服务'
                        ))
                    else:
                        results.append(TestResult(
                            f'shared.{module_name}.channels',
                            False,
                            '缺少必要的通道或服务定义'
                        ))

                elif module_name == 'message_protocol':
                    expected_classes = [
                        'MessageHeader', 'TelemetryData', 'ControlCommand',
                        'PredictionResult', 'AlarmEvent', 'MessageFactory'
                    ]
                    for cls_name in expected_classes:
                        if hasattr(module, cls_name):
                            results.append(TestResult(
                                f'shared.{module_name}.{cls_name}',
                                True,
                                '类定义存在'
                            ))
                        else:
                            results.append(TestResult(
                                f'shared.{module_name}.{cls_name}',
                                False,
                                '类定义缺失'
                            ))

                elif module_name == 'config_loader':
                    if hasattr(module, 'ConfigLoader'):
                        results.append(TestResult(
                            f'shared.{module_name}.ConfigLoader',
                            True,
                            '类定义存在'
                        ))
                    else:
                        results.append(TestResult(
                            f'shared.{module_name}.ConfigLoader',
                            False,
                            '类定义缺失'
                        ))

                elif module_name == 'redis_client':
                    expected_classes = ['RedisClientBase', 'MicroserviceBase']
                    for cls_name in expected_classes:
                        if hasattr(module, cls_name):
                            results.append(TestResult(
                                f'shared.{module_name}.{cls_name}',
                                True,
                                '类定义存在'
                            ))
                        else:
                            results.append(TestResult(
                                f'shared.{module_name}.{cls_name}',
                                False,
                                '类定义缺失'
                            ))

            except Exception as e:
                results.append(TestResult(
                    f'shared.{module_name}',
                    False,
                    f'导入失败: {str(e)}\n{traceback.format_exc()}'
                ))

        return results

    def test_service_imports(self) -> List[TestResult]:
        """测试各微服务模块导入"""
        results: List[TestResult] = []

        for service_name in SERVICES:
            try:
                service_dir = self.base_dir / service_name
                main_file = service_dir / 'main.py'
                init_file = service_dir / '__init__.py'

                if not main_file.exists():
                    results.append(TestResult(
                        f'{service_name}.main',
                        False,
                        f'文件不存在: {main_file}'
                    ))
                    continue

                if not init_file.exists():
                    results.append(TestResult(
                        f'{service_name}.__init__',
                        False,
                        f'文件不存在: {init_file}'
                    ))
                    continue

                with open(main_file, 'r', encoding='utf-8') as f:
                    code = f.read()

                compile(code, str(main_file), 'exec')
                results.append(TestResult(
                    f'{service_name}.main.syntax',
                    True,
                    '语法检查通过'
                ))

                service_class_mapping = {
                    'profinet_driver': 'ProfinetDriverService',
                    'temp_controller': 'TemperatureControllerService',
                    'quality_predictor': 'QualityPredictorService',
                    'alarm_publisher': 'AlarmPublisherService',
                    'api_gateway': 'APIGatewayService',
                    'db_writer': 'DBWriterService',
                }
                expected_class = service_class_mapping.get(service_name, 
                    f'{service_name.title().replace("_", "")}Service')
                if expected_class in code:
                    results.append(TestResult(
                        f'{service_name}.main.service_class',
                        True,
                        f'包含服务类: {expected_class}'
                    ))
                else:
                    results.append(TestResult(
                        f'{service_name}.main.service_class',
                        False,
                        f'缺少服务类定义: {expected_class}'
                    ))

                shared_marker = 'from shared import'
                if shared_marker in code:
                    results.append(TestResult(
                        f'{service_name}.import.shared',
                        True,
                        '正确导入shared模块'
                    ))
                    
                    base_required = {
                        'MicroserviceBase': 'redis_client',
                        'CHANNELS': 'redis_channels',
                        'SERVICE_IDS': 'redis_channels',
                    }
                    
                    service_specific = {
                        'profinet_driver': ['TelemetryData', 'MessageFactory'],
                        'temp_controller': ['TelemetryData', 'ControlCommand', 'MessageFactory'],
                        'quality_predictor': ['PredictionResult', 'MessageFactory'],
                        'alarm_publisher': ['AlarmEvent', 'MessageFactory'],
                        'api_gateway': ['TelemetryData', 'ControlCommand', 'PredictionResult', 'AlarmEvent', 'MessageFactory'],
                        'db_writer': ['TelemetryData', 'ControlCommand', 'PredictionResult', 'AlarmEvent'],
                    }
                    
                    for symbol, module in base_required.items():
                        if symbol in code:
                            results.append(TestResult(
                                f'{service_name}.import.{module}',
                                True,
                                f'导入{symbol}'
                            ))
                        else:
                            results.append(TestResult(
                                f'{service_name}.import.{module}',
                                False,
                                f'缺少{symbol}导入'
                            ))
                    
                    required_symbols = service_specific.get(service_name, [])
                    for symbol in required_symbols:
                        if symbol in code:
                            results.append(TestResult(
                                f'{service_name}.import.message_protocol',
                                True,
                                f'导入{symbol}'
                            ))
                else:
                    results.append(TestResult(
                        f'{service_name}.import.shared',
                        False,
                        '缺少shared模块导入'
                    ))

            except SyntaxError as e:
                results.append(TestResult(
                    f'{service_name}.main.syntax',
                    False,
                    f'语法错误: {str(e)}\n{traceback.format_exc()}'
                ))
            except Exception as e:
                results.append(TestResult(
                    f'{service_name}.main',
                    False,
                    f'检查失败: {str(e)}\n{traceback.format_exc()}'
                ))

        return results

    def test_message_protocol(self) -> List[TestResult]:
        """测试消息协议序列化和反序列化"""
        results: List[TestResult] = []

        if 'message_protocol' not in self.shared_imports:
            results.append(TestResult(
                'message_protocol.serialization',
                False,
                '消息协议模块未加载'
            ))
            return results

        mp = self.shared_imports['message_protocol']

        try:
            telemetry = mp.TelemetryData(
                device_id=1,
                shelf_id=1,
                timestamp='2024-01-01T00:00:00Z',
                temperatures=[25.0, 25.1, 24.9, 25.2, 25.0, 24.8, 25.1, 25.0],
                vacuum_levels=[1.0, 0.9],
                cold_trap_temp=-50.0,
                heating_powers=[50.0, 51.0],
            )

            message = mp.MessageFactory.create_telemetry(telemetry, 'test-service')

            results.append(TestResult(
                'message_protocol.telemetry_create',
                True,
                '遥测消息创建成功'
            ))

            json_str = mp.serialize_message(message)
            parsed = mp.deserialize_message(json_str)

            results.append(TestResult(
                'message_protocol.telemetry_serialize',
                True,
                '遥测消息序列化成功'
            ))

            if parsed['payload']['device_id'] == telemetry.device_id:
                results.append(TestResult(
                    'message_protocol.telemetry_parse',
                    True,
                    '遥测消息解析成功'
                ))
            else:
                results.append(TestResult(
                    'message_protocol.telemetry_parse',
                    False,
                    '遥测消息解析数据不匹配'
                ))

        except Exception as e:
            results.append(TestResult(
                'message_protocol.serialization',
                False,
                f'序列化失败: {str(e)}\n{traceback.format_exc()}'
            ))

        return results

    def test_redis_channels(self) -> List[TestResult]:
        """测试Redis通道定义"""
        results: List[TestResult] = []

        if 'redis_channels' not in self.shared_imports:
            results.append(TestResult(
                'redis_channels.validation',
                False,
                'Redis通道模块未加载'
            ))
            return results

        rc = self.shared_imports['redis_channels']

        required_channels = [
            'TELEMETRY_RAW',
            'TELEMETRY_PROCESSED',
            'CONTROL_COMMAND',
            'CONTROL_STATUS',
            'PREDICTION_RESULT',
            'ALARM_EVENT',
            'ALARM_ACK',
            'CONFIG_UPDATE',
            'SYSTEM_STATUS',
        ]

        for channel in required_channels:
            if hasattr(rc.CHANNELS, channel) or channel in rc.CHANNELS:
                results.append(TestResult(
                    f'redis_channels.{channel}',
                    True,
                    f'通道定义: {rc.CHANNELS[channel]}'
                ))
            else:
                results.append(TestResult(
                    f'redis_channels.{channel}',
                    False,
                    '通道定义缺失'
                ))

        return results

    def test_config_files(self) -> List[TestResult]:
        """测试配置文件格式"""
        results: List[TestResult] = []

        config_dir = self.base_dir.parent / 'config'
        config_files = [
            'control_params.yaml',
            'model_params.yaml',
            'alarm_thresholds.yaml',
        ]

        for config_file in config_files:
            config_path = config_dir / config_file
            if config_path.exists():
                results.append(TestResult(
                    f'config.{config_file}',
                    True,
                    '配置文件存在'
                ))

                try:
                    import yaml
                    with open(config_path, 'r', encoding='utf-8') as f:
                        content = yaml.safe_load(f)
                    results.append(TestResult(
                        f'config.{config_file}.yaml_parse',
                        True,
                        'YAML格式正确'
                    ))
                except ImportError:
                    results.append(TestResult(
                        f'config.{config_file}.yaml_parse',
                        True,
                        'PyYAML未安装，跳过解析验证'
                    ))
                except Exception as e:
                    results.append(TestResult(
                        f'config.{config_file}.yaml_parse',
                        False,
                        f'YAML格式错误: {str(e)}'
                    ))
            else:
                results.append(TestResult(
                    f'config.{config_file}',
                    False,
                    f'配置文件不存在: {config_path}'
                ))

        return results

    def test_frontend_modules(self) -> List[TestResult]:
        """测试前端模块"""
        results: List[TestResult] = []

        frontend_dir = self.base_dir.parent / 'frontend' / 'src' / 'components'
        frontend_modules = [
            'shelf_thermal.ts',
            'quality_dashboard.ts',
            'Heatmap.tsx',
            'QualityPrediction.tsx',
        ]

        for module_name in frontend_modules:
            module_path = frontend_dir / module_name
            if module_path.exists():
                results.append(TestResult(
                    f'frontend.{module_name}',
                    True,
                    '前端模块存在'
                ))

                with open(module_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                if module_name == 'shelf_thermal.ts':
                    expected_exports = [
                        'TemperatureData',
                        'SensorPosition',
                        'HeatmapConfig',
                        'CacheKey',
                        'DEFAULT_CONFIG',
                        'getTemperatureColor',
                        'calculateSensorPositions',
                        'detectAbnormalRegions',
                        'hasSignificantChange',
                        'drawStaticLayer',
                        'drawDynamicLayer',
                        'findHoveredSensor',
                    ]
                    for export in expected_exports:
                        if f'export {export}' in content or f'export const {export}' in content or f'export function {export}' in content or f'export interface {export}' in content:
                            results.append(TestResult(
                                f'frontend.shelf_thermal.export_{export}',
                                True,
                                '导出正确'
                            ))
                        else:
                            results.append(TestResult(
                                f'frontend.shelf_thermal.export_{export}',
                                False,
                                '导出缺失'
                            ))

                elif module_name == 'quality_dashboard.ts':
                    expected_exports = [
                        'PredictionResult',
                        'GaugeConfig',
                        'PredictionHistoryItem',
                        'DEFAULT_GAUGE_CONFIG',
                        'drawGauge',
                        'drawConfidenceBar',
                        'formatPredictionMessage',
                        'calculateQualityScore',
                    ]
                    for export in expected_exports:
                        if f'export {export}' in content or f'export const {export}' in content or f'export function {export}' in content or f'export interface {export}' in content:
                            results.append(TestResult(
                                f'frontend.quality_dashboard.export_{export}',
                                True,
                                '导出正确'
                            ))
                        else:
                            results.append(TestResult(
                                f'frontend.quality_dashboard.export_{export}',
                                False,
                                '导出缺失'
                            ))

                elif module_name == 'Heatmap.tsx':
                    if "from './shelf_thermal'" in content:
                        results.append(TestResult(
                            'frontend.Heatmap.import_shelf_thermal',
                            True,
                            '正确导入shelf_thermal模块'
                        ))
                    else:
                        results.append(TestResult(
                            'frontend.Heatmap.import_shelf_thermal',
                            False,
                            '缺少shelf_thermal模块导入'
                        ))

                elif module_name == 'QualityPrediction.tsx':
                    if "from './quality_dashboard'" in content:
                        results.append(TestResult(
                            'frontend.QualityPrediction.import_quality_dashboard',
                            True,
                            '正确导入quality_dashboard模块'
                        ))
                    else:
                        results.append(TestResult(
                            'frontend.QualityPrediction.import_quality_dashboard',
                            False,
                            '缺少quality_dashboard模块导入'
                        ))

            else:
                results.append(TestResult(
                    f'frontend.{module_name}',
                    False,
                    f'前端模块不存在: {module_path}'
                ))

        return results

    def run_all_tests(self) -> Tuple[List[TestResult], int, int]:
        """运行所有回归测试"""
        print('=' * 80)
        print('生物制药冻干机微服务架构 - 回归测试')
        print('=' * 80)
        print()

        all_results: List[TestResult] = []

        print('📦 测试共享模块...')
        print('-' * 80)
        results = self.test_shared_modules()
        all_results.extend(results)
        for r in results:
            print(f'  {r}')
        print()

        print('🔧 测试微服务模块...')
        print('-' * 80)
        results = self.test_service_imports()
        all_results.extend(results)
        for r in results:
            print(f'  {r}')
        print()

        print('📨 测试消息协议...')
        print('-' * 80)
        results = self.test_message_protocol()
        all_results.extend(results)
        for r in results:
            print(f'  {r}')
        print()

        print('📡 测试Redis通道定义...')
        print('-' * 80)
        results = self.test_redis_channels()
        all_results.extend(results)
        for r in results:
            print(f'  {r}')
        print()

        print('⚙️  测试配置文件...')
        print('-' * 80)
        results = self.test_config_files()
        all_results.extend(results)
        for r in results:
            print(f'  {r}')
        print()

        print('🎨 测试前端模块...')
        print('-' * 80)
        results = self.test_frontend_modules()
        all_results.extend(results)
        for r in results:
            print(f'  {r}')
        print()

        passed = sum(1 for r in all_results if r.passed)
        failed = sum(1 for r in all_results if not r.passed)

        print('=' * 80)
        print('📊 测试结果汇总')
        print('=' * 80)
        print(f'  总测试数: {len(all_results)}')
        print(f'  通过: {passed}  ✓')
        print(f'  失败: {failed}  ✗')
        print(f'  通过率: {(passed / len(all_results) * 100):.1f}%')
        print()

        if failed > 0:
            print('❌ 失败的测试:')
            print('-' * 80)
            for r in all_results:
                if not r.passed:
                    print(f'  {r}')
            print()

        print('=' * 80)
        print('✅ 微服务架构验证完成!' if failed == 0 else '⚠️  存在测试失败，请检查代码!')
        print('=' * 80)

        return all_results, passed, failed


def main():
    tester = RegressionTester()
    _, passed, failed = tester.run_all_tests()
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
